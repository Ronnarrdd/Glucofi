/*

     download samples from a Roche accuchek device using libusb

     usage: see kUsage below, or run accuchek --help

     --set-time  set the meter clock to the PC clock when they differ by more
                 than kClockToleranceS and the PC clock is NTP synchronized
     --now       PC clock to assume while replaying, taken as synchronized

     stdout: a JSON object (format 2, see schema/output.schema.json),
             written only once the download succeeded
     stderr: "accuchek: <reason>" on failure, logs when ACCUCHEK_DBG is set
     exit codes: see ExitCode in session.h

     compile with: make

 */

// stuff we need
#include <log.h>
#include <trace.h>
#include <output.h>
#include <session.h>
#include <protocol.h>
#include <string>
#include <vector>
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <unistd.h>
#include <stdarg.h>
#include <memory>
#include <algorithm>
#include <libusb-1.0/libusb.h>

extern bool gQuiet;

using namespace accuchek;

// globals
static Config g_config = defaultConfig();
static std::vector<Sample> g_samples;
static SessionOptions g_options;
static SessionReport g_report;
static std::string g_accessDenied;  // last known meter we were not allowed to open

// reason the program stops, reported on stderr with its exit code
struct Fatal {
    ExitCode code;
    std::string msg;
};

[[noreturn]] static void die(
    ExitCode code,
    const char *format,
    ...
) {
    char msg[1024];
    va_list arg;
    va_start(arg, format);
    vsnprintf(msg, sizeof(msg), format, arg);
    va_end(arg);
    LOG_WRN("%s -- giving up", msg);
    throw Fatal{code, msg};
}

// add a config file to the built-in device list
static void loadConfig(
    const char *path
) {
    std::string text;
    if(false==readFile(path, text)) {
        die(kExitUsage, "cannot read config file %s", path);
    }
    g_config = configWithFile(text);
}

// a usb device (only things about the device we actually need)
struct USBDevice {

    // data
    libusb_device *dev;
    uint16_t vendorId;
    uint16_t productId;
    std::string vendor;
    std::string product;
    uint8_t sndEndPoint;
    uint8_t rcvEndPoint;
    uint8_t configValue;
    uint8_t interfaceNumber;
    uint8_t alternateSetting;

    // constructor
    USBDevice(
        libusb_device *_dev,
        uint16_t _vendorId,
        uint16_t _productId,
        const char *_vendor,
        const char *_product,
        uint8_t _sndEndPoint,   // NB: used to write _to_ device _from_ host
        uint8_t _rcvEndPoint,   // NB: used to read _from_ device _to_ host
        const libusb_config_descriptor *cfg,
        const libusb_interface_descriptor *altSetting
    )
        :   dev(_dev),
            vendorId(_vendorId),
            productId(_productId),
            vendor(_vendor),
            product(_product),
            sndEndPoint(_sndEndPoint),
            rcvEndPoint(_rcvEndPoint),
            configValue(cfg->bConfigurationValue),
            interfaceNumber(altSetting->bInterfaceNumber),
            alternateSetting(altSetting->bAlternateSetting)
    {
        // increase refcount on libusb device handle
        libusb_ref_device(dev);
    }

    // copy constructor
    USBDevice(
        const USBDevice &rhs
    )
        :   dev(rhs.dev),
            vendorId(rhs.vendorId),
            productId(rhs.productId),
            vendor(rhs.vendor),
            product(rhs.product),
            sndEndPoint(rhs.sndEndPoint),
            rcvEndPoint(rhs.rcvEndPoint),
            configValue(rhs.configValue),
            interfaceNumber(rhs.interfaceNumber),
            alternateSetting(rhs.alternateSetting)
    {
        // increase refcount on libusb device handle
        libusb_ref_device(dev);
    }
    USBDevice &operator=(const USBDevice &) = delete;

    // destructor
    ~USBDevice() {
        // decrease refcount on libusb device handle
        libusb_unref_device(dev);
    }

    // show device specs
    void show(
        const char *msg
    ) {
        LOG_NFO(
            "%s:\n"
            "\n"
            "    bus number:    %d\n"
            "    dev address:   %d\n"
            "    cfg value:     %d\n"
            "    alt setting:   %d\n"
            "    alt interface number: %d\n"
            "    vendor:        (0x%04x) %s\n"
            "    product:       (0x%04x) %s\n"
            "    sndEndPnt:     %d\n"
            "    rcvEndPnt:     %d\n"
            ,
            msg,
            libusb_get_bus_number(dev),
            libusb_get_device_address(dev),
            (int)configValue,
            (int)alternateSetting,
            (int)interfaceNumber,
            (int)vendorId,
            vendor.c_str(),
            (int)productId,
            product.c_str(),
            sndEndPoint,
            rcvEndPoint
        );
    }
};

// transport over an opened and claimed libusb device
struct LibusbTransport : Transport {

    LibusbTransport(
        libusb_device_handle *_devHandle,
        uint8_t _sndEndPoint,
        uint8_t _rcvEndPoint
    )
        :   devHandle(_devHandle),
            sndEndPoint(_sndEndPoint),
            rcvEndPoint(_rcvEndPoint)
    {
    }

    int controlStatus(
        uint8_t *buffer,
        size_t len
    ) override {
        return libusb_control_transfer(
            devHandle,
            (
                LIBUSB_REQUEST_TYPE_STANDARD |
                LIBUSB_RECIPIENT_DEVICE      |
                LIBUSB_ENDPOINT_IN
            ),
            LIBUSB_REQUEST_GET_STATUS,
            0,
            0,
            buffer,
            len,
            kTimeoutMs
        );
    }

    int bulkOut(
        const uint8_t *buffer,
        size_t len
    ) override {
        int bytesWritten = -1;
        auto fail = libusb_bulk_transfer(
            devHandle,
            sndEndPoint,
            const_cast<uint8_t *>(buffer),
            len,
            &bytesWritten,
            kTimeoutMs
        );
        return (0!=fail ? fail : bytesWritten);
    }

    int bulkIn(
        uint8_t *buffer,
        size_t maxLen
    ) override {
        int bytesRead = 0;
        auto fail = libusb_bulk_transfer(
            devHandle,
            rcvEndPoint,
            buffer,
            maxLen,
            &bytesRead,
            kTimeoutMs
        );
        return (0!=fail ? fail : bytesRead);
    }

    const char *errorName(
        int code
    ) override {
        return libusb_strerror(code);
    }

    static constexpr unsigned kTimeoutMs = 5000;
    libusb_device_handle *devHandle;
    uint8_t sndEndPoint;
    uint8_t rcvEndPoint;
};

/*

    syslog excerpt from an actual accuchek connecting:

        May 26 14:35:34 machine kernel: [598051.987118] usb 3-1: new full-speed USB device number 59 using xhci_hcd
        May 26 14:35:34 machine kernel: [598052.141001] usb 3-1: New USB device found, idVendor=173a, idProduct=21d5, bcdDevice= 1.00
        May 26 14:35:34 machine kernel: [598052.141005] usb 3-1: New USB device strings: Mfr=1, Product=2, SerialNumber=0
        May 26 14:35:34 machine kernel: [598052.141007] usb 3-1: Product: ACCU-CHEK Guide
        May 26 14:35:34 machine kernel: [598052.141008] usb 3-1: Manufacturer: Roche

    lsusb -v excerpt:

        Bus 003Device 118: ID 173a:21d5 Roche
          idVendor           0x173a Roche
          idProduct          0x21d5
          bNumConfigurations      1
          Configuration Descriptor:
            bNumInterfaces          1
            bConfigurationValue     1
            Interface Descriptor:
              bInterfaceNumber        0
              bAlternateSetting       0
              bNumEndpoints           2
              bInterfaceClass        15
              iInterface              4 Personal Healthcare Device Class
              Endpoint Descriptor:
                bEndpointAddress     0x81  EP 1 IN
                bmAttributes            2
                  Transfer Type            Bulk
                wMaxPacketSize     0x0040  1x 64 bytes
              Endpoint Descriptor:
                bEndpointAddress     0x01  EP 1 OUT
                bmAttributes            2
                  Transfer Type            Bulk
                wMaxPacketSize     0x0040  1x 64 bytes

    endpoints:

        bEndpointAddress     0x81  EP 1 IN      (device to host)
        bEndpointAddress     0x01  EP 1 OUT     (host to device)
        rcvEndPnt:     129
        sndEndPnt:     1

*/

// run the protocol, keep samples in memory until it succeeds
static void runSession(
    Transport &transport
) {
    try {
        downloadSamples(transport, g_options, g_report, [](const Sample &s) { g_samples.push_back(s); });
    } catch(const SessionError &e) {
        auto received = g_report.glucose.received;
        g_samples.clear();
        if(0<received) {
            die(e.code, "%s (%d samples received before the error, none written)", e.what(), (int)received);
        }
        die(e.code, "%s", e.what());
    }
}

// exit code for a failed libusb call on the meter
static ExitCode usbFailure(
    int code
) {
    return (LIBUSB_ERROR_ACCESS==code ? kExitAccessDenied : kExitTransfer);
}

// an opened device handle, closed on every path out
struct DeviceHandle {
    libusb_device_handle *handle = 0;
    DeviceHandle() = default;
    DeviceHandle(const DeviceHandle &) = delete;
    DeviceHandle &operator=(const DeviceHandle &) = delete;
    ~DeviceHandle() {
        if(0!=handle) {
            libusb_close(handle);
        }
    }
};

// the meter interface, given back on every path out: released, then the
// kernel driver we detached (if any) is reattached
struct ClaimedInterface {
    libusb_device_handle *handle;
    int interface;
    bool claimed = false;
    bool driverDetached = false;
    ClaimedInterface(libusb_device_handle *_handle, int _interface) : handle(_handle), interface(_interface) {}
    ClaimedInterface(const ClaimedInterface &) = delete;
    ClaimedInterface &operator=(const ClaimedInterface &) = delete;
    ~ClaimedInterface() {
        if(claimed) {
            libusb_release_interface(handle, interface);
        }
        if(driverDetached) {
            libusb_attach_kernel_driver(handle, interface);
        }
    }
};

// open an accuchek USB device and download data from it
static void operateDevice(
    USBDevice &usbDevice,
    const char *capturePath
) {
    // open device
    DeviceHandle device;
    auto fail0 = libusb_open(usbDevice.dev, &device.handle);
    if(fail0) {
        device.handle = 0;
        die(usbFailure(fail0), "cannot open meter: %s", libusb_strerror(fail0));
    }
    auto devHandle = device.handle;
    ClaimedInterface interface(devHandle, usbDevice.interfaceNumber);

    // detach whatever kernel driver may have been attached to it (none for a
    // PHDC meter on Linux: LIBUSB_ERROR_NOT_FOUND)
    interface.driverDetached = (0==libusb_detach_kernel_driver(devHandle, usbDevice.interfaceNumber));

    // load the configuration chosen during detection phase
    auto fail1 = libusb_set_configuration(devHandle, usbDevice.configValue);
    if(fail1<0) {
        die(usbFailure(fail1), "cannot configure meter: %s", libusb_strerror(fail1));
    }

    // claim interface
    auto fail2 = libusb_claim_interface(devHandle, usbDevice.interfaceNumber);
    if(fail2<0) {
        die(usbFailure(fail2), "cannot claim meter interface: %s", libusb_strerror(fail2));
    }
    interface.claimed = true;

    // set alt setting chosen during detection phase on interface
    auto fail3 = libusb_set_interface_alt_setting(devHandle, usbDevice.interfaceNumber, usbDevice.alternateSetting);
    if(fail3<0) {
        die(usbFailure(fail3), "cannot set meter alt setting: %s", libusb_strerror(fail3));
    }

    // make some noise
    LOG_NFO("using device snd endpoint = %d", usbDevice.sndEndPoint);
    LOG_NFO("using device rcv endpoint = %d\n", usbDevice.rcvEndPoint);

    LibusbTransport usb(devHandle, usbDevice.sndEndPoint, usbDevice.rcvEndPoint);
    if(0!=capturePath) {
        auto fp = fopen(capturePath, "w");
        if(0==fp) {
            die(kExitUsage, "cannot write trace %s", capturePath);
        }
        // keep the trace of a failed download too, that is when it is most useful
        auto closeTrace = [&]() {
            auto failed = (0!=ferror(fp));
            failed = (0!=fclose(fp)) || failed;
            if(failed) {
                fprintf(stderr, "accuchek: warning: cannot write trace %s, it is incomplete\n", capturePath);
            }
        };
        RecordingTransport recording(usb, fp);
        try {
            runSession(recording);
        } catch(const Fatal &) {
            closeTrace();
            throw;
        }
        closeTrace();
    } else {
        runSession(usb);
    }
    LOG_NFO("closing usb device");
}

// process one USB device and add it to the list if it matches requirements
static void addDeviceIfAccuChek(
    std::vector<USBDevice> &validDevices,
    libusb_device *dev
) {

    // get USB device description
    libusb_device_descriptor dsc;
    auto fail = libusb_get_device_descriptor(dev, &dsc);
    if(0!=fail) {
        LOG_WRN("libusb_get_device_descriptor failed");
        return;
    }

    // only look closer at known meters, never open anything else
    if(!isDeviceAllowed(g_config, dsc.idVendor, dsc.idProduct)) {
        LOG_NFO("not a match, %04x:%04x is not a known meter", (int)dsc.idVendor, (int)dsc.idProduct);
        return;
    }

    // ugly trick to "goto done" over declarations using a break
    struct libusb_config_descriptor *cfg = 0;
    do {

        // accuchek has one config, anything with more or less is a bust
        if(1!=dsc.bNumConfigurations) {
            LOG_NFO("not a match, too many configs to be an accuchek");
            break;
        }

        // load first config
        auto confIndex = 0;
        auto fail0 = libusb_get_config_descriptor(dev, confIndex, &cfg);
        if(0!=fail0) {
            LOG_WRN("libusb_get_device_descriptor failed");
            break;
        }

        // accuchek single config has one interface, anything with more or less is a bust
        if(1!=cfg->bNumInterfaces) {
            LOG_NFO("not a match, too many interfaces to be an accuchek");
            break;
        }

        // look at all "alt settings" for first interface
        auto interfaceIndex = 0;
        auto interface = &(cfg->interface[interfaceIndex]);

        // accuchek single config has one alt setting, anything with more or less is a bust
        if(1!=interface->num_altsetting) {
            LOG_NFO("not a match, too many alt settings to be an accuchek");
            break;
        }

        // accuchek has two endpoints, anything with more or less is a bust
        int altSettingIndex = 0;
        auto altSetting = &(interface->altsetting[altSettingIndex]);
        if(2!=altSetting->bNumEndpoints) {
            LOG_NFO("not a match, too many endpoints to be an accuchek");
            break;
        }

        // look for endpoints on altsetting that match our criteria
        auto in = 0;    // 0 is an invalid endpoint
        auto out = 0;   // 0 is an invalid endpoint
        for(auto endPointIndex=0; endPointIndex<altSetting->bNumEndpoints; ++endPointIndex) {
            // accuchek endpoints should both have a max packet size of 64
            auto endPoint = &(altSetting->endpoint[endPointIndex]);
            if(64==endPoint->wMaxPacketSize) {
                // device must be non-interrupt type
                auto attributes = (endPoint->bmAttributes & LIBUSB_TRANSFER_TYPE_MASK);
                auto isInterrupt = (LIBUSB_TRANSFER_TYPE_INTERRUPT==attributes);
                if(false==isInterrupt) {
                    // LIBUSB_ENDPOINT_IN means endpoint is used to transfer date from the device to the host
                    auto isInput = (LIBUSB_ENDPOINT_IN & endPoint->bEndpointAddress);
                    if(isInput) {
                        in = endPoint->bEndpointAddress;
                    } else {
                        out = endPoint->bEndpointAddress;
                    }
                }
            }
        }

        // skip devices that dont have at least one input and one output
        auto ok = (0!=in && 0!=out);
        if(false==ok) {
            LOG_NFO("not a match, need at least one input endpoint and one output endpoint");
            break;
        }

        // we found a device seems to fit the bill, open it
        LOG_NFO("found a usb device that looks good, checking further by opening it");
        DeviceHandle probe;
        auto fail1 = libusb_open(dev, &probe.handle);
        if(fail1) {
            probe.handle = 0;
            LOG_WRN("libusb_open failed: %s", libusb_strerror(fail1));
            if(LIBUSB_ERROR_ACCESS==fail1) {
                char where[128];
                snprintf(
                    where,
                    sizeof(where),
                    "%04x:%04x on bus %03d device %03d",
                    (int)dsc.idVendor,
                    (int)dsc.idProduct,
                    (int)libusb_get_bus_number(dev),
                    (int)libusb_get_device_address(dev)
                );
                g_accessDenied = where;
            }
            break;
        }

        // get device vendor
        char vendor[512];
        memset(vendor, 0, sizeof(vendor));
        auto r0 = libusb_get_string_descriptor_ascii(
            probe.handle,
            dsc.iManufacturer,
            (uint8_t*)vendor,
            (-1+sizeof(vendor))
        );
        if(r0<0) {
            LOG_NFO("not a match, vendorId unreadable");
            break;
        }

        // get product id
        char product[512];
        memset(product, 0, sizeof(product));
        auto r1 = libusb_get_string_descriptor_ascii(
            probe.handle,
            dsc.iProduct,
            (uint8_t*)product,
            (-1+sizeof(product))
        );
        if(r1<0) {
            LOG_NFO("not a match, productId unreadable");
            break;
        }

        // we have a new valid device, add it to the list
        LOG_NFO("========> found a matching USB device: mfgr=%s device=%s", vendor, product);
        validDevices.emplace_back(
            dev,
            dsc.idVendor,
            dsc.idProduct,
            vendor,
            product,
            out,
            in,
            cfg,
            altSetting
        );
    } while(0);

    // free config data structure
    libusb_free_config_descriptor(cfg);
}

// find all possible accuchek devices, pick one and download data from it
static void findAndOperateAccuChek(
    libusb_context *libUSBContext,
    int ix,
    const char *capturePath
) {

    // obtain a list of all USB devices in the system
    libusb_device **devices = 0;
    LOG_NFO("getting list of all USB devices in system from libusb");
    auto count = libusb_get_device_list(libUSBContext, &devices);
    LOG_NFO("found %d USB devices in system", (int)count);

    // check them one by one and add to the list if specs are a match
    std::vector<USBDevice> validDevices;
    LOG_NFO("searching for valid accuchek devices");
    for(int i=0; i<count; ++i) {
        LOG_NFO("checking if device %d is an accuchek", (int)i);
        addDeviceIfAccuChek(validDevices, devices[i]);
    }

    // clean up device list
    libusb_free_device_list(devices, 1);

    // if no devices found, bail
    if(0==validDevices.size()) {
        if(!g_accessDenied.empty()) {
            die(kExitAccessDenied, "permission denied on USB meter %s", g_accessDenied.c_str());
        }
        die(kExitNoDevice, "no Accu-Chek meter found on the USB bus");
    }

    // make some noise
    LOG_NFO(
        "found altogether %d accuchek devices",
        (int)validDevices.size()
    );

    // make sure we user referes to a valid device
    if(int(validDevices.size())<=int(ix)) {
        die(kExitNoDevice, "meter #%d selected but only %d found", ix, (int)validDevices.size());
    }

    // select a specific device (first seen or as specified by user)
    auto selectedIndex = std::max(0, ix);
    auto &selectedDevice = validDevices[selectedIndex];

    // show details of selected device as gathered from libusb
    char buf[256];
    snprintf(
        buf,
        sizeof(buf),
        "selecting accuchek device #%d:",
        (int)selectedIndex
    );
    selectedDevice.show(buf);

    // talk to device to download data from it
    operateDevice(selectedDevice, capturePath);
}

// the libusb context, exited on every path out (after every handle and
// device it gave is released: declare it first)
struct UsbContext {
    libusb_context *context = 0;
    UsbContext() {
        LOG_NFO("opening libusb");
        auto fail = libusb_init(&context);
        if(0!=fail || 0==context) {
            context = 0;
            die(kExitTransfer, "cannot initialize libusb: %s", libusb_strerror(fail));
        }
        LOG_NFO("libusb opened OK");
    }
    UsbContext(const UsbContext &) = delete;
    UsbContext &operator=(const UsbContext &) = delete;
    ~UsbContext() {
        if(0!=context) {
            LOG_NFO("closing libusb");
            libusb_exit(context);
        }
    }
};

// replay a recorded trace instead of talking to a device
static void replayTrace(
    const char *path
) {
    std::string text;
    if(false==readFile(path, text)) {
        die(kExitUsage, "cannot read trace %s", path);
    }
    std::unique_ptr<ReplayTransport> replay;
    try {
        replay.reset(new ReplayTransport(text));
    } catch(const std::runtime_error &e) {
        die(kExitUsage, "bad trace %s: %s", path, e.what());
    }
    runSession(*replay);
}

// set by the Makefile from git describe or the VERSION file
#ifndef ACCUCHEK_VERSION
#define ACCUCHEK_VERSION "unknown"
#endif

static const char kUsage[] =
    "usage: accuchek [DEVICE_INDEX] [--config FILE] [--set-time] [--capture TRACE]\n"
    "       accuchek --replay TRACE [--set-time --now \"YYYY/MM/DD HH:MM:SS\"]\n"
    "       accuchek [--config FILE] --known-devices\n"
    "       accuchek --help | --version\n"
    "\n"
    "Download every reading from a Roche Accu-Chek meter over USB and print\n"
    "them as one JSON object on stdout.\n"
    "\n"
    "  DEVICE_INDEX      read the Nth known meter on the bus (default: the first)\n"
    "  --set-time        set the meter clock to the PC clock if they differ by\n"
    "                    more than 60 s and the PC clock is NTP synchronized\n"
    "  --capture TRACE   also record the USB exchange to TRACE (health data!)\n"
    "  --replay TRACE    replay a recorded exchange instead of talking to a meter\n"
    "  --now TIME        PC clock to assume while replaying\n"
    "  --config FILE     add or disable meter models (see config.example.txt)\n"
    "  --known-devices   list accepted meters as vendor:product\n"
    "\n"
    "Exit codes: 0 ok, 1 usage, 2 no meter, 3 access denied (udev rule missing),\n"
    "4 USB transfer failed, 5 protocol error, 6 stdout not writable.\n"
    "Set ACCUCHEK_DBG=1 for logs on stderr.\n";

// everything but writing on stdout: returns what to write, throws Fatal on failure
static std::string run(
    int argc,
    char *argv[]
) {

    // parse command line
    int deviceIndex = -1;
    const char *capturePath = 0;
    const char *replayPath = 0;
    const char *configPath = 0;
    const char *nowText = 0;
    bool listDevices = false;
    // an option given twice is a typo or a script bug, never "last one wins"
    auto value = [&](int &i, const char *&slot) {
        if(0!=slot) {
            die(kExitUsage, "%s given twice", argv[i]);
        }
        slot = argv[++i];
    };
    for(int i=1; i<argc; ++i) {
        if(0==strcmp(argv[i], "--help") || 0==strcmp(argv[i], "-h")) {
            return kUsage;
        } else if(0==strcmp(argv[i], "--version")) {
            return std::string("accuchek ") + ACCUCHEK_VERSION + "\n";
        } else if(0==strcmp(argv[i], "--config") && i+1<argc) {
            value(i, configPath);
        } else if(0==strcmp(argv[i], "--set-time")) {
            g_options.setTime = true;
        } else if(0==strcmp(argv[i], "--now") && i+1<argc) {
            value(i, nowText);
        } else if(0==strcmp(argv[i], "--known-devices")) {
            listDevices = true;
        } else if(0==strcmp(argv[i], "--capture") && i+1<argc) {
            value(i, capturePath);
        } else if(0==strcmp(argv[i], "--replay") && i+1<argc) {
            value(i, replayPath);
        } else if('-'!=argv[i][0]) {
            // atoi used to turn "foo" into meter #0
            char *end = 0;
            errno = 0;
            auto n = strtol(argv[i], &end, 10);
            if(!isdigit((unsigned char)argv[i][0]) || 0!=*end || 0!=errno || 9999<n) {
                die(kExitUsage, "bad DEVICE_INDEX %s, expected 0, 1, 2... (see accuchek --help)", argv[i]);
            }
            if(0<=deviceIndex) {
                die(kExitUsage, "DEVICE_INDEX given twice");
            }
            deviceIndex = int(n);
        } else {
            die(kExitUsage, "unknown or incomplete option %s (see accuchek --help)", argv[i]);
        }
    }
    if(0!=replayPath && 0!=capturePath) {
        die(kExitUsage, "--capture records a meter, it does not go with --replay");
    }
    if(0!=replayPath && 0<=deviceIndex) {
        die(kExitUsage, "DEVICE_INDEX selects a meter, it does not go with --replay");
    }

    if(0!=configPath) {
        loadConfig(configPath);
    }
    // replaying a trace: the PC clock of the capture is unknown unless given
    if(0==replayPath) {
        g_options.pcClock = systemClock;
    }
    if(0!=nowText) {
        // a fake clock must never reach a real meter
        if(0==replayPath) {
            die(kExitUsage, "--now only goes with --replay");
        }
        struct tm t;
        memset(&t, 0, sizeof(t));
        int consumed = 0;
        if(6!=sscanf(nowText, "%d/%d/%d %d:%d:%d%n", &t.tm_year, &t.tm_mon, &t.tm_mday, &t.tm_hour, &t.tm_min, &t.tm_sec, &consumed) ||
            0!=nowText[consumed]) {
            die(kExitUsage, "bad --now %s, expected \"YYYY/MM/DD HH:MM:SS\"", nowText);
        }
        t.tm_year -= 1900;
        t.tm_mon -= 1;
        t.tm_isdst = -1;
        auto asked = t;
        auto now = mktime(&t);
        // mktime normalizes 2026/13/45 into 2027/02/14 and shifts times in the
        // spring forward gap: a date that does not come back unchanged is wrong
        if(asked.tm_year!=t.tm_year || asked.tm_mon!=t.tm_mon || asked.tm_mday!=t.tm_mday ||
            asked.tm_hour!=t.tm_hour || asked.tm_min!=t.tm_min || asked.tm_sec!=t.tm_sec) {
            die(kExitUsage, "bad --now %s, no such local time", nowText);
        }
        g_options.pcClock = [now]() { return PcClock{now, true, true}; };
    }
    if(listDevices) {
        std::string list;
        for(const auto &device : allowedDevices(g_config)) {
            list += device + "\n";
        }
        return list;
    }

    // make some noise
    LOG_NFO("starting");

    if(0!=replayPath) {
        replayTrace(replayPath);
    } else {
        // open libusb
        UsbContext usb;

        // find and talk to one accuchek device
        findAndOperateAccuChek(usb.context, deviceIndex, capturePath);
    }
    for(const auto &warning : countWarnings(g_report)) {
        fprintf(stderr, "accuchek: warning: %s\n", warning.c_str());
    }
    return outputJson(g_report, g_samples);
}

// a full disk or a closed pipe must not end in exit code 0
static void writeStdout(
    const std::string &text
) {
    auto written = fwrite(text.data(), 1, text.size(), stdout);
    auto flushed = (0==fflush(stdout));
    auto error = errno;
    if(written!=text.size() || !flushed || ferror(stdout)) {
        die(kExitOutput, "cannot write on stdout: %s", strerror(error));
    }
    if(0!=fclose(stdout)) {
        die(kExitOutput, "cannot write on stdout: %s", strerror(errno));
    }
}

// entry point
int main(
    int argc,
    char *argv[]
) {
    // be silent unless asked to talk (on stderr)
    gQuiet = (0==getenv("ACCUCHEK_DBG"));
    try {
        // a closed fd 1 would be reused by the next open, a --capture trace
        // would then receive the JSON: refuse before talking to the meter
        if(fcntl(STDOUT_FILENO, F_GETFD)<0) {
            die(kExitOutput, "stdout is closed, nowhere to write the readings");
        }
        writeStdout(run(argc, argv));
    } catch(const Fatal &f) {
        fprintf(stderr, "accuchek: %s\n", f.msg.c_str());
        return f.code;
    }
    LOG_NFO("done");
    return kExitOk;
}
