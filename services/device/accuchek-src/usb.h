/*

    finding and opening a meter over libusb

    the decisions are kept out of libusb calls so tests can drive them:
    findMeterInterface reads descriptors, ClaimedMeter runs the open sequence
    over DeviceOps, waitFor polls with an injected clock

 */

#ifndef __USB_H__
    #define __USB_H__

    #include <session.h>
    #include <string>
    #include <vector>
    #include <stdexcept>
    #include <functional>
    #include <libusb-1.0/libusb.h>

    namespace accuchek {

    // where to talk to a meter, from its USB descriptors
    struct MeterInterface {
        uint8_t configValue = 0;
        uint8_t interfaceNumber = 0;
        uint8_t alternateSetting = 0;
        uint8_t inEndpoint = 0;     // device to host
        uint8_t outEndpoint = 0;    // host to device
    };

    // a PHDC meter has one configuration with one interface, one alt setting
    // and two non interrupt endpoints of 64 bytes, one in, one out; false with
    // whyNot set for anything else
    bool findMeterInterface(const libusb_config_descriptor &cfg, MeterInterface &found, std::string &whyNot);

    // failed libusb call on the way to a claimed meter
    struct UsbError : std::runtime_error {
        UsbError(ExitCode _code, const std::string &msg) : std::runtime_error(msg), code(_code) {}
        ExitCode code;
    };

    // exit code for a libusb error: access denied means the udev rule is missing
    ExitCode usbFailure(int libusbCode);

    // the libusb calls made on an opened meter (libusb_get_configuration...),
    // same return values; faked in tests
    struct DeviceOps {
        virtual ~DeviceOps() = default;
        virtual int getConfiguration(int &config) = 0;
        virtual int setConfiguration(int config) = 0;
        virtual int detachKernelDriver(int interface) = 0;
        virtual int attachKernelDriver(int interface) = 0;
        virtual int claimInterface(int interface) = 0;
        virtual int releaseInterface(int interface) = 0;
        virtual int setAltSetting(int interface, int alt) = 0;
        virtual const char *errorName(int code) = 0;
    };

    // the meter interface, claimed for the lifetime of the object
    //
    // the configuration is only set when another one is active: setting the
    // active one again is a lightweight device reset (libusb documentation),
    // and Linux configures the meter when it enumerates. Throws UsbError; on
    // every path out the interface is released and the kernel driver detached
    // here (none for a PHDC meter) is reattached
    class ClaimedMeter {
    public:
        ClaimedMeter(DeviceOps &ops, const MeterInterface &meter);
        ~ClaimedMeter();
        ClaimedMeter(const ClaimedMeter &) = delete;
        ClaimedMeter &operator=(const ClaimedMeter &) = delete;

    private:
        void giveBack();
        DeviceOps &ops;
        int interface;
        bool claimed = false;
        bool driverDetached = false;
    };

    // calls ready() every periodMs until it returns true or timeoutMs have
    // passed, returns the last answer; ready() runs at least once
    bool waitFor(
        const std::function<bool()> &ready,
        long timeoutMs,
        long periodMs,
        const std::function<long()> &nowMs,
        const std::function<void(long)> &sleepMs
    );

    // monotonic clock and sleep for waitFor
    long monotonicMs();
    void sleepMs(long ms);

    // libusb context, exited on every path out; declare it before anything
    // it gives (devices, handles)
    struct UsbContext {
        UsbContext();   // throws UsbError
        ~UsbContext();
        UsbContext(const UsbContext &) = delete;
        UsbContext &operator=(const UsbContext &) = delete;
        libusb_context *context = 0;
    };

    // a known meter on the bus; holds a reference on the libusb device
    struct FoundMeter {
        FoundMeter(libusb_device *dev, uint16_t vendorId, uint16_t productId,
            const std::string &vendor, const std::string &product, const MeterInterface &interface);
        FoundMeter(const FoundMeter &rhs);
        FoundMeter &operator=(const FoundMeter &) = delete;
        ~FoundMeter();

        libusb_device *dev;
        uint16_t vendorId;
        uint16_t productId;
        std::string vendor;
        std::string product;
        MeterInterface interface;
    };

    struct MeterScan {
        std::vector<FoundMeter> meters;
        std::string accessDenied;   // last known meter we were not allowed to open, "" if none
    };

    // every known meter on the bus, in libusb order; nothing else is opened
    MeterScan scanMeters(libusb_context *context, const Config &config);

    // open and claim the meter, give fn a transport over it, release on
    // every path out; throws UsbError when the meter cannot be claimed
    void withMeter(const FoundMeter &meter, const std::function<void(Transport &)> &fn);

    } // namespace accuchek

#endif // __USB_H__
