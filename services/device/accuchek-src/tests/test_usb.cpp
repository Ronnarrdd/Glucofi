#include "check.h"
#include <usb.h>

using namespace accuchek;

// descriptors of an Accu-Chek Guide (lsusb -v): configuration 1, interface 0,
// alt setting 0, bulk endpoints 0x81 in and 0x01 out of 64 bytes
struct GuideDescriptors {
    libusb_endpoint_descriptor endpoints[3] = {};
    libusb_interface_descriptor altSettings[2] = {};
    libusb_interface interfaces[2] = {};
    libusb_config_descriptor cfg = {};

    GuideDescriptors() {
        endpoints[0].bEndpointAddress = 0x81;
        endpoints[0].bmAttributes = LIBUSB_TRANSFER_TYPE_BULK;
        endpoints[0].wMaxPacketSize = 64;
        endpoints[1].bEndpointAddress = 0x01;
        endpoints[1].bmAttributes = LIBUSB_TRANSFER_TYPE_BULK;
        endpoints[1].wMaxPacketSize = 64;
        endpoints[2] = endpoints[1];
        altSettings[0].bInterfaceNumber = 0;
        altSettings[0].bAlternateSetting = 0;
        altSettings[0].bInterfaceClass = 15;    // Personal Healthcare Device Class
        altSettings[0].bNumEndpoints = 2;
        altSettings[0].endpoint = endpoints;
        altSettings[1] = altSettings[0];
        interfaces[0].altsetting = altSettings;
        interfaces[0].num_altsetting = 1;
        interfaces[1] = interfaces[0];
        cfg.bConfigurationValue = 1;
        cfg.bNumInterfaces = 1;
        cfg.interface = interfaces;
    }

    std::string whyNot() {
        MeterInterface found;
        std::string why;
        CHECK(!findMeterInterface(cfg, found, why));
        return why;
    }
};

TEST(usb_guide_descriptors_are_a_meter) {
    GuideDescriptors d;
    MeterInterface found;
    std::string why;
    CHECK(findMeterInterface(d.cfg, found, why));
    CHECK_EQ((int)found.configValue, 1);
    CHECK_EQ((int)found.interfaceNumber, 0);
    CHECK_EQ((int)found.alternateSetting, 0);
    CHECK_EQ((int)found.inEndpoint, 0x81);
    CHECK_EQ((int)found.outEndpoint, 0x01);
    CHECK_EQ(why, std::string(""));
}

TEST(usb_other_descriptors_are_not_a_meter) {
    {
        GuideDescriptors d;
        d.cfg.bNumInterfaces = 2;
        CHECK_EQ(d.whyNot(), std::string("2 interfaces, a meter has 1"));
    }
    {
        GuideDescriptors d;
        d.interfaces[0].num_altsetting = 2;
        CHECK_EQ(d.whyNot(), std::string("2 alt settings, a meter has 1"));
    }
    {
        GuideDescriptors d;
        d.altSettings[0].bNumEndpoints = 3;
        CHECK_EQ(d.whyNot(), std::string("3 endpoints, a meter has 2"));
    }
    {
        GuideDescriptors d;
        d.endpoints[0].bmAttributes = LIBUSB_TRANSFER_TYPE_INTERRUPT;
        CHECK_EQ(d.whyNot(), std::string("no pair of 64 byte in and out endpoints"));
    }
    {
        GuideDescriptors d;
        d.endpoints[1].wMaxPacketSize = 512;
        CHECK_EQ(d.whyNot(), std::string("no pair of 64 byte in and out endpoints"));
    }
    {
        GuideDescriptors d;
        d.endpoints[1].bEndpointAddress = 0x82;     // two in endpoints
        CHECK_EQ(d.whyNot(), std::string("no pair of 64 byte in and out endpoints"));
    }
}

// records every libusb call; each answer can be overridden
struct FakeOps : DeviceOps {
    std::string calls;
    int activeConfig = 1;
    int getResult = 0;
    int setResult = 0;
    int detachResult = LIBUSB_ERROR_NOT_FOUND;
    int claimResult = 0;
    int altResult = 0;

    int getConfiguration(int &config) override { calls += "get "; config = activeConfig; return getResult; }
    int setConfiguration(int config) override { calls += "set" + std::to_string(config) + " "; return setResult; }
    int detachKernelDriver(int) override { calls += "detach "; return detachResult; }
    int attachKernelDriver(int) override { calls += "attach "; return 0; }
    int claimInterface(int) override { calls += "claim "; return claimResult; }
    int releaseInterface(int) override { calls += "release "; return 0; }
    int setAltSetting(int, int alt) override { calls += "alt" + std::to_string(alt) + " "; return altResult; }
    const char *errorName(int code) override { return libusb_error_name(code); }
};

static MeterInterface guideInterface() {
    MeterInterface m;
    m.configValue = 1;
    m.inEndpoint = 0x81;
    m.outEndpoint = 0x01;
    return m;
}

// ClaimedMeter on ops, "ok" or the UsbError it throws
static std::string claim(
    FakeOps &ops,
    ExitCode *code = 0
) {
    try {
        ClaimedMeter claimed(ops, guideInterface());
        ops.calls += "| ";
    } catch(const UsbError &e) {
        if(code) {
            *code = e.code;
        }
        return e.what();
    }
    return "ok";
}

// setting the active configuration again used to reset the meter on every run
TEST(usb_active_configuration_is_not_set_again) {
    FakeOps ops;
    CHECK_EQ(claim(ops), std::string("ok"));
    CHECK_EQ(ops.calls, std::string("detach get claim alt0 | release "));
}

TEST(usb_unconfigured_meter_gets_its_configuration) {
    FakeOps ops;
    ops.activeConfig = 0;
    CHECK_EQ(claim(ops), std::string("ok"));
    CHECK_EQ(ops.calls, std::string("detach get set1 claim alt0 | release "));

    FakeOps unreadable;
    unreadable.getResult = LIBUSB_ERROR_IO;
    CHECK_EQ(claim(unreadable), std::string("ok"));
    CHECK_EQ(unreadable.calls, std::string("detach get set1 claim alt0 | release "));

    // the platform configures the device itself
    FakeOps unsupported;
    unsupported.activeConfig = 0;
    unsupported.setResult = LIBUSB_ERROR_NOT_SUPPORTED;
    CHECK_EQ(claim(unsupported), std::string("ok"));
}

TEST(usb_claim_failures_give_everything_back) {
    ExitCode code = kExitOk;

    FakeOps denied;
    denied.claimResult = LIBUSB_ERROR_ACCESS;
    CHECK_EQ(claim(denied, &code), std::string("cannot claim meter interface: LIBUSB_ERROR_ACCESS"));
    CHECK_EQ(code, kExitAccessDenied);
    CHECK_EQ(denied.calls, std::string("detach get claim "));

    FakeOps busy;
    busy.claimResult = LIBUSB_ERROR_BUSY;
    CHECK_EQ(claim(busy, &code), std::string("meter busy, another program is reading it: LIBUSB_ERROR_BUSY"));
    CHECK_EQ(code, kExitTransfer);

    // a kernel driver detached here is reattached on the way out
    FakeOps alt;
    alt.detachResult = 0;
    alt.altResult = LIBUSB_ERROR_PIPE;
    CHECK_EQ(claim(alt, &code), std::string("cannot set meter alt setting: LIBUSB_ERROR_PIPE"));
    CHECK_EQ(code, kExitTransfer);
    CHECK_EQ(alt.calls, std::string("detach get claim alt0 release attach "));

    FakeOps config;
    config.activeConfig = 2;
    config.setResult = LIBUSB_ERROR_BUSY;
    CHECK_EQ(claim(config, &code), std::string("cannot configure meter: LIBUSB_ERROR_BUSY"));
    CHECK_EQ(config.calls, std::string("detach get set1 "));

    FakeOps detached;
    detached.detachResult = 0;
    CHECK_EQ(claim(detached), std::string("ok"));
    CHECK_EQ(detached.calls, std::string("detach get claim alt0 | release attach "));
}

// fake clock: sleeping moves it forward
struct FakeClock {
    long now = 1000;
    std::string sleeps;
    std::function<long()> nowMs() { return [this]() { return now; }; }
    std::function<void(long)> sleepMs() {
        return [this](long ms) { sleeps += std::to_string(ms) + " "; now += ms; };
    }
};

TEST(usb_wait_for_polls_until_ready) {
    FakeClock clock;
    int calls = 0;
    CHECK(waitFor([&]() { return 3==++calls; }, 10000, 500, clock.nowMs(), clock.sleepMs()));
    CHECK_EQ(calls, 3);
    CHECK_EQ(clock.sleeps, std::string("500 500 "));
}

TEST(usb_wait_for_gives_up_on_time) {
    FakeClock clock;
    int calls = 0;
    CHECK(!waitFor([&]() { ++calls; return false; }, 1200, 500, clock.nowMs(), clock.sleepMs()));
    // the last sleep is cut to the deadline, one last look after it
    CHECK_EQ(clock.sleeps, std::string("500 500 200 "));
    CHECK_EQ(calls, 4);

    FakeClock once;
    int onceCalls = 0;
    CHECK(!waitFor([&]() { ++onceCalls; return false; }, 0, 500, once.nowMs(), once.sleepMs()));
    CHECK_EQ(onceCalls, 1);
    CHECK_EQ(once.sleeps, std::string(""));
}

TEST(usb_failure_exit_codes) {
    CHECK_EQ(usbFailure(LIBUSB_ERROR_ACCESS), kExitAccessDenied);
    CHECK_EQ(usbFailure(LIBUSB_ERROR_TIMEOUT), kExitTransfer);
    CHECK_EQ(usbFailure(LIBUSB_ERROR_NO_DEVICE), kExitTransfer);
}
