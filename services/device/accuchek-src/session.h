/*

    one download session with an accuchek device, over an abstract transport
    (libusb in production, recorded traces in tests)

 */

#ifndef __SESSION_H__
    #define __SESSION_H__

    #include <protocol.h>
    #include <string>
    #include <stdexcept>
    #include <functional>

    namespace accuchek {

    // byte pipe to the device; negative return values are transport error codes
    struct Transport {
        virtual ~Transport() = default;

        // standard GET_STATUS control transfer in, returns bytes read
        virtual int controlStatus(uint8_t *buffer, size_t len) = 0;

        // bulk transfer to the device, returns bytes written
        virtual int bulkOut(const uint8_t *buffer, size_t len) = 0;

        // bulk transfer from the device, returns bytes read
        virtual int bulkIn(uint8_t *buffer, size_t maxLen) = 0;

        virtual const char *errorName(int code) = 0;

        // comment for a recorded trace ("# " + text), ignored by other transports
        virtual void note(const std::string &) {}
    };

    // process exit codes, part of the public interface: callers branch on them
    enum ExitCode {
        kExitOk = 0,            // all samples written (none if the meter is empty)
        kExitUsage = 1,         // bad arguments, unreadable config, trace or capture file
        kExitNoDevice = 2,      // no known meter on the USB bus
        kExitAccessDenied = 3,  // meter found but not allowed to open it
        kExitTransfer = 4,      // USB transfer failed: timeout, meter unplugged
        kExitProtocol = 5,      // meter aborted or answered something unexpected
        kExitOutput = 6,        // stdout closed or not writable (disk full...): readings lost
    };

    struct SessionError : std::runtime_error {
        SessionError(ExitCode _code, const std::string &msg) : std::runtime_error(msg), code(_code) {}
        ExitCode code;
    };

    // the PC clock, as seen when the meter clock is read
    struct PcClock {
        time_t now;
        bool known;         // false when replaying a trace without --now
        bool synchronized;  // kernel clock disciplined by NTP, trusted to set the meter
    };

    // meter clock further than this from the PC clock gets set, when asked to
    static constexpr long kClockToleranceS = 60;

    // data messages read from one segment before giving up on its "last" flag:
    // a Guide stores at most 720 readings and sends at least one per message,
    // so a meter going past this never ends; bounds memory (1 KB per message)
    static constexpr size_t kMaxDataMessages = 4096;

    struct SessionOptions {
        bool setTime = false;
        std::function<PcClock()> pcClock;   // PC clock unknown when empty
        size_t maxDataMessages = kMaxDataMessages;
    };

    // why the meter clock was or was not set
    enum ClockAction {
        kClockNotRequested = 0,
        kClockSet,
        kClockWithinTolerance,
        kClockNotSettable,          // the meter does not advertise mds-time-capab-set-clock
        kClockPcNotSynchronized,
        kClockPcUnknown,
        kClockUnknown,              // the meter did not give its clock
        kClockRejected,             // the meter answered the set time action with an error
    };
    const char *clockActionName(ClockAction action);

    struct SegmentCount {
        bool announced = false;     // the meter gave its usage count
        uint32_t expected = 0;
        uint32_t received = 0;
    };

    // everything learnt besides the samples
    struct SessionReport {
        bool hasMeter = false;
        MeterInfo meter;
        PcClock pc = {0, false, false};
        bool hasClockOffset = false;
        long clockOffsetS = 0;      // meter clock minus PC clock
        ClockAction clockAction = kClockNotRequested;
        SegmentCount glucose;
        bool hasMealSegment = false;
        SegmentCount meal;
        size_t mealsUnmatched = 0;
    };

    // run the whole protocol: glucose samples, then meal markers when the meter
    // stores them, attached to their samples before onSample is called; every
    // sample is reported whatever its status; an empty meter is not an error
    // (no sample), neither is a failed release nor a refused clock setting
    void downloadSamples(
        Transport &transport,
        const SessionOptions &options,
        SessionReport &report,
        const std::function<void(const Sample &)> &onSample
    );

    // same, without setting the clock nor keeping the report
    void downloadSamples(
        Transport &transport,
        const std::function<void(const Sample &)> &onSample
    );

    // PC clock from time() and adjtimex()
    PcClock systemClock();

    // canonical hexdump of a buffer with header (debug output)
    void hexDumpWithHeader(const char *bufferName, const uint8_t *buffer, uint32_t size);

    } // namespace accuchek

#endif // __SESSION_H__
