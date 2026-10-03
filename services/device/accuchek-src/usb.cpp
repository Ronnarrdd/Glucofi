#include <usb.h>
#include <log.h>
#include <errno.h>
#include <time.h>
#include <stdio.h>
#include <string.h>
#include <algorithm>

namespace accuchek {

bool findMeterInterface(
    const libusb_config_descriptor &cfg,
    MeterInterface &found,
    std::string &whyNot
) {
    if(1!=cfg.bNumInterfaces) {
        whyNot = std::to_string(cfg.bNumInterfaces) + " interfaces, a meter has 1";
        return false;
    }
    const auto &interface = cfg.interface[0];
    if(1!=interface.num_altsetting) {
        whyNot = std::to_string(interface.num_altsetting) + " alt settings, a meter has 1";
        return false;
    }
    const auto &altSetting = interface.altsetting[0];
    if(2!=altSetting.bNumEndpoints) {
        whyNot = std::to_string(altSetting.bNumEndpoints) + " endpoints, a meter has 2";
        return false;
    }
    uint8_t in = 0;     // 0 is not a valid endpoint address
    uint8_t out = 0;
    for(int i=0; i<altSetting.bNumEndpoints; ++i) {
        const auto &endPoint = altSetting.endpoint[i];
        auto type = (endPoint.bmAttributes & LIBUSB_TRANSFER_TYPE_MASK);
        if(64!=endPoint.wMaxPacketSize || LIBUSB_TRANSFER_TYPE_INTERRUPT==type) {
            continue;
        }
        if(LIBUSB_ENDPOINT_IN & endPoint.bEndpointAddress) {
            in = endPoint.bEndpointAddress;
        } else {
            out = endPoint.bEndpointAddress;
        }
    }
    if(0==in || 0==out) {
        whyNot = "no pair of 64 byte in and out endpoints";
        return false;
    }
    found.configValue = cfg.bConfigurationValue;
    found.interfaceNumber = altSetting.bInterfaceNumber;
    found.alternateSetting = altSetting.bAlternateSetting;
    found.inEndpoint = in;
    found.outEndpoint = out;
    return true;
}

ExitCode usbFailure(
    int libusbCode
) {
    return (LIBUSB_ERROR_ACCESS==libusbCode ? kExitAccessDenied : kExitTransfer);
}

ClaimedMeter::ClaimedMeter(
    DeviceOps &_ops,
    const MeterInterface &meter
)
    :   ops(_ops),
        interface(meter.interfaceNumber)
{
    auto fail = [&](const char *what, int code) {
        throw UsbError(usbFailure(code), std::string(what) + ": " + ops.errorName(code));
    };
    try {
        // detach whatever kernel driver may have been attached to it (none
        // for a PHDC meter on Linux: LIBUSB_ERROR_NOT_FOUND)
        driverDetached = (0==ops.detachKernelDriver(interface));

        int active = -1;
        auto got = ops.getConfiguration(active);
        if(0!=got) {
            LOG_WRN("cannot read the active configuration (%s), setting it", ops.errorName(got));
        }
        if(0!=got || active!=meter.configValue) {
            LOG_NFO("active configuration %d, selecting %d", active, (int)meter.configValue);
            auto set = ops.setConfiguration(meter.configValue);
            // the platform configures the device itself: safe to ignore
            if(set<0 && LIBUSB_ERROR_NOT_SUPPORTED!=set) {
                fail("cannot configure meter", set);
            }
        }

        auto claim = ops.claimInterface(interface);
        if(LIBUSB_ERROR_BUSY==claim) {
            fail("meter busy, another program is reading it", claim);
        }
        if(claim<0) {
            fail("cannot claim meter interface", claim);
        }
        claimed = true;

        auto alt = ops.setAltSetting(interface, meter.alternateSetting);
        if(alt<0) {
            fail("cannot set meter alt setting", alt);
        }
    } catch(...) {
        giveBack();
        throw;
    }
}

ClaimedMeter::~ClaimedMeter() {
    giveBack();
}

void ClaimedMeter::giveBack() {
    if(claimed) {
        ops.releaseInterface(interface);
        claimed = false;
    }
    if(driverDetached) {
        ops.attachKernelDriver(interface);
        driverDetached = false;
    }
}

bool waitFor(
    const std::function<bool()> &ready,
    long timeoutMs,
    long periodMs,
    const std::function<long()> &nowMs,
    const std::function<void(long)> &sleep
) {
    auto deadline = nowMs() + timeoutMs;
    while(true) {
        if(ready()) {
            return true;
        }
        auto left = deadline - nowMs();
        if(left<=0) {
            return false;
        }
        sleep(std::min(periodMs, left));
    }
}

long monotonicMs() {
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return long(now.tv_sec) * 1000 + now.tv_nsec / 1000000;
}

void sleepMs(
    long ms
) {
    struct timespec left = {ms / 1000, (ms % 1000) * 1000000};
    while(0!=nanosleep(&left, &left) && EINTR==errno) {
    }
}

UsbContext::UsbContext() {
    LOG_NFO("opening libusb");
    auto fail = libusb_init(&context);
    if(0!=fail || 0==context) {
        context = 0;
        throw UsbError(kExitTransfer, std::string("cannot initialize libusb: ") + libusb_strerror(fail));
    }
}

UsbContext::~UsbContext() {
    if(0!=context) {
        LOG_NFO("closing libusb");
        libusb_exit(context);
    }
}

FoundMeter::FoundMeter(
    libusb_device *_dev,
    uint16_t _vendorId,
    uint16_t _productId,
    const std::string &_vendor,
    const std::string &_product,
    const MeterInterface &_interface
)
    :   dev(libusb_ref_device(_dev)),
        vendorId(_vendorId),
        productId(_productId),
        vendor(_vendor),
        product(_product),
        interface(_interface)
{
}

FoundMeter::FoundMeter(
    const FoundMeter &rhs
)
    :   dev(libusb_ref_device(rhs.dev)),
        vendorId(rhs.vendorId),
        productId(rhs.productId),
        vendor(rhs.vendor),
        product(rhs.product),
        interface(rhs.interface)
{
}

FoundMeter::~FoundMeter() {
    libusb_unref_device(dev);
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

// ASCII string descriptor, false when unreadable
static bool stringDescriptor(
    libusb_device_handle *handle,
    uint8_t index,
    std::string &text
) {
    unsigned char buffer[256];
    auto n = libusb_get_string_descriptor_ascii(handle, index, buffer, sizeof(buffer));
    if(n<0) {
        return false;
    }
    text.assign((const char *)buffer, size_t(n));
    return true;
}

// add dev to the scan when it is a known meter whose descriptors fit
static void scanDevice(
    MeterScan &scan,
    const Config &config,
    libusb_device *dev
) {
    libusb_device_descriptor dsc;
    if(0!=libusb_get_device_descriptor(dev, &dsc)) {
        LOG_WRN("libusb_get_device_descriptor failed");
        return;
    }

    // only look closer at known meters, never open anything else
    if(!isDeviceAllowed(config, dsc.idVendor, dsc.idProduct)) {
        LOG_NFO("not a match, %04x:%04x is not a known meter", (int)dsc.idVendor, (int)dsc.idProduct);
        return;
    }
    if(1!=dsc.bNumConfigurations) {
        LOG_NFO("not a match, %d configurations, a meter has 1", (int)dsc.bNumConfigurations);
        return;
    }
    libusb_config_descriptor *cfg = 0;
    if(0!=libusb_get_config_descriptor(dev, 0, &cfg)) {
        LOG_WRN("libusb_get_config_descriptor failed");
        return;
    }
    MeterInterface interface;
    std::string whyNot;
    auto fits = findMeterInterface(*cfg, interface, whyNot);
    libusb_free_config_descriptor(cfg);
    if(!fits) {
        LOG_NFO("not a match, %s", whyNot.c_str());
        return;
    }

    // opening it tells whether the udev rule lets us in
    DeviceHandle probe;
    auto fail = libusb_open(dev, &probe.handle);
    if(0!=fail) {
        probe.handle = 0;
        LOG_WRN("libusb_open failed: %s", libusb_strerror(fail));
        if(LIBUSB_ERROR_ACCESS==fail) {
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
            scan.accessDenied = where;
        }
        return;
    }
    std::string vendor;
    std::string product;
    if(!stringDescriptor(probe.handle, dsc.iManufacturer, vendor) || !stringDescriptor(probe.handle, dsc.iProduct, product)) {
        LOG_NFO("not a match, vendor or product string unreadable");
        return;
    }
    LOG_NFO("found a meter: %s %s", vendor.c_str(), product.c_str());
    scan.meters.emplace_back(dev, dsc.idVendor, dsc.idProduct, vendor, product, interface);
}

MeterScan scanMeters(
    libusb_context *context,
    const Config &config
) {
    MeterScan scan;
    libusb_device **devices = 0;
    auto count = libusb_get_device_list(context, &devices);
    LOG_NFO("found %d USB devices", (int)count);
    for(ssize_t i=0; i<count; ++i) {
        scanDevice(scan, config, devices[i]);
    }
    if(0<=count) {
        libusb_free_device_list(devices, 1);
    }
    return scan;
}

// DeviceOps over an opened libusb handle
struct LibusbOps : DeviceOps {
    explicit LibusbOps(libusb_device_handle *_handle) : handle(_handle) {}
    int getConfiguration(int &config) override { return libusb_get_configuration(handle, &config); }
    int setConfiguration(int config) override { return libusb_set_configuration(handle, config); }
    int detachKernelDriver(int interface) override { return libusb_detach_kernel_driver(handle, interface); }
    int attachKernelDriver(int interface) override { return libusb_attach_kernel_driver(handle, interface); }
    int claimInterface(int interface) override { return libusb_claim_interface(handle, interface); }
    int releaseInterface(int interface) override { return libusb_release_interface(handle, interface); }
    int setAltSetting(int interface, int alt) override { return libusb_set_interface_alt_setting(handle, interface, alt); }
    const char *errorName(int code) override { return libusb_strerror(code); }
    libusb_device_handle *handle;
};

// transport over a claimed meter interface
struct LibusbTransport : Transport {

    LibusbTransport(
        libusb_device_handle *_handle,
        const MeterInterface &_meter
    )
        :   handle(_handle),
            meter(_meter)
    {
    }

    int controlStatus(
        uint8_t *buffer,
        size_t len
    ) override {
        return libusb_control_transfer(
            handle,
            (LIBUSB_REQUEST_TYPE_STANDARD | LIBUSB_RECIPIENT_DEVICE | LIBUSB_ENDPOINT_IN),
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
        int written = -1;
        auto fail = libusb_bulk_transfer(handle, meter.outEndpoint, const_cast<uint8_t *>(buffer), len, &written, kTimeoutMs);
        return (0!=fail ? fail : written);
    }

    int bulkIn(
        uint8_t *buffer,
        size_t maxLen
    ) override {
        int read = 0;
        auto fail = libusb_bulk_transfer(handle, meter.inEndpoint, buffer, maxLen, &read, kTimeoutMs);
        return (0!=fail ? fail : read);
    }

    const char *errorName(
        int code
    ) override {
        return libusb_strerror(code);
    }

    static constexpr unsigned kTimeoutMs = 5000;
    libusb_device_handle *handle;
    MeterInterface meter;
};

void withMeter(
    const FoundMeter &meter,
    const std::function<void(Transport &)> &fn
) {
    LOG_NFO(
        "opening %04x:%04x (%s %s) on bus %d device %d: configuration %d, interface %d, alt setting %d, endpoints in 0x%02x out 0x%02x",
        (int)meter.vendorId,
        (int)meter.productId,
        meter.vendor.c_str(),
        meter.product.c_str(),
        (int)libusb_get_bus_number(meter.dev),
        (int)libusb_get_device_address(meter.dev),
        (int)meter.interface.configValue,
        (int)meter.interface.interfaceNumber,
        (int)meter.interface.alternateSetting,
        (int)meter.interface.inEndpoint,
        (int)meter.interface.outEndpoint
    );
    DeviceHandle device;
    auto fail = libusb_open(meter.dev, &device.handle);
    if(0!=fail) {
        device.handle = 0;
        throw UsbError(usbFailure(fail), std::string("cannot open meter: ") + libusb_strerror(fail));
    }
    LibusbOps ops(device.handle);
    ClaimedMeter claimed(ops, meter.interface);
    LibusbTransport transport(device.handle, meter.interface);
    fn(transport);
    LOG_NFO("closing the meter");
}

} // namespace accuchek
