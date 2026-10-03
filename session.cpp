#include <log.h>
#include <session.h>
#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/timex.h>


namespace accuchek {

// canonical hexdump of a buffer
static void hexDump(
    const uint8_t *buffer,
    uint32_t size
) {
    uint32_t i = 0;
    while(i<size) {
        auto e = (16 + i);
        for(auto j=i; j<e; ++j) {
            if(j<size) {
                fprintf(stderr, "%02X ", buffer[j]);
            } else {
                fprintf(stderr, "   ");
            }
        }
        fprintf(stderr, "   ");
        for(auto j=i; j<e; ++j) {
            if(j<size) {
                auto c = buffer[j];
                fputc(isprint(c) ? c : '.', stderr);
            }
        }
        fputc('\n', stderr);
        i = e;
    }
}

void hexDumpWithHeader(
    const char *bufferName,
    const uint8_t *buffer,
    uint32_t size
) {
    if(gQuiet) {
        return;
    }
    LOG_NFO(
        "hexdump of buffer:\n\nBUFFER START \"%s\" size=%d (0x%x) ===============================================",
        bufferName,
        (int)size,
        (int)size
    );
    hexDump(buffer, size);
    fprintf(stderr, "BUFFER END ============================================================================================\n\n");
}

const char *clockActionName(
    ClockAction action
) {
    switch(action) {
        case kClockNotRequested: return "not_requested";
        case kClockSet: return "set";
        case kClockWithinTolerance: return "within_tolerance";
        case kClockNotSettable: return "not_settable";
        case kClockPcNotSynchronized: return "pc_not_synchronized";
        case kClockPcUnknown: return "pc_unknown";
        case kClockUnknown: return "unknown";
        case kClockRejected: return "rejected";
    }
    return "unknown";
}

PcClock systemClock() {
    struct timex tx;
    memset(&tx, 0, sizeof(tx));     // modes = 0: read only, no privilege needed
    auto state = adjtimex(&tx);
    return {time(0), true, 0<=state && TIME_ERROR!=state};
}

static ClockAction decideClock(
    const SessionOptions &options,
    const SessionReport &report
) {
    if(!options.setTime) {
        return kClockNotRequested;
    }
    if(!report.hasMeter || !report.meter.hasClock) {
        return kClockUnknown;
    }
    if(!report.meter.clockSettable) {
        return kClockNotSettable;
    }
    if(!report.pc.known) {
        return kClockPcUnknown;
    }
    if(!report.pc.synchronized) {
        return kClockPcNotSynchronized;
    }
    if(labs(report.clockOffsetS)<=kClockToleranceS) {
        return kClockWithinTolerance;
    }
    return kClockSet;
}

/*
   much of what follows was directly reverse-engineered from the highly
   unportable (only works in effing Chrome) javascript code found here:

       https://github.com/tidepool-org/uploader/tree/master/lib/drivers/roche

   and backported to raw libusb. The original author of the tidepool code
   likely had access to a manual documenting the protocol.
*/
void downloadSamples(
    Transport &transport,
    const SessionOptions &options,
    SessionReport &report,
    const std::function<void(const Sample &)> &onSample
) {
    uint8_t buffer[kBufferSize];
    memset(buffer, 0, sizeof(buffer));
    uint16_t invokeId = -1;
    size_t received = 0;    // bytes of the last message received into buffer
    int phaseIndex = 1;
    report = SessionReport();

    auto fail = [&](
        ExitCode code,
        const std::string &msg
    ) {
        LOG_WRN("%s -- giving up", msg.c_str());
        throw SessionError(code, msg);
    };

    // send out the message at the start of buffer
    auto send = [&](
        const char *msgName,
        size_t len
    ) {
        LOG_NFO("phase %d: sending message %s", phaseIndex, msgName);
        hexDumpWithHeader(msgName, buffer, len);
        auto written = transport.bulkOut(buffer, len);
        if(written<0 || size_t(written)!=len) {
            fail(
                kExitTransfer,
                std::string("failed to send message ") + msgName + ": " +
                (written<0 ? transport.errorName(written) : "short write")
            );
        }
        LOG_NFO("successfully wrote message %s, size=%d (0x%x):", msgName, (int)len, (int)len);
        ++phaseIndex;
    };

    // receive a message into buffer
    auto receive = [&](
        const char *msgName,
        size_t maxLen = kBufferSize
    ) {
        LOG_NFO("phase %d: receiving message %s", phaseIndex, msgName);
        auto bytesRead = transport.bulkIn(buffer, maxLen);
        if(bytesRead<0) {
            fail(kExitTransfer, std::string("failed to receive message ") + msgName + ": " + transport.errorName(bytesRead));
        }
        LOG_NFO("successfully read message \"%s\" from device", msgName);
        hexDumpWithHeader(msgName, buffer, bytesRead);
        ++phaseIndex;
        received = bytesRead;

        // the device may abort the association instead of answering
        Reader r(buffer, received);
        if(kAPDU_TYPE_ASSOCIATION_ABORT==r.u16() && r.ok) {
            fail(kExitProtocol, std::string("received association abort request instead of ") + msgName);
        }
        return bytesRead;
    };

    auto updateInvokeId = [&]() {
        if(!readInvokeId(buffer, received, invokeId)) {
            fail(kExitProtocol, "message too short to hold an invoke id (" + std::to_string(received) + " bytes)");
        }
        LOG_NFO("invokeId after phase %d is: %d", phaseIndex, (int)invokeId);
    };

    // protocol step: do a control transfer in
    {
        #define PHASE_1 "initial control transfer in"
        LOG_NFO("phase 1: " PHASE_1);
        auto bytesRead = transport.controlStatus(buffer, 2);
        if(bytesRead<0) {
            fail(kExitTransfer, std::string("failed " PHASE_1 ": ") + transport.errorName(bytesRead));
        }
        if(0==bytesRead) {
            fail(kExitTransfer, "failed " PHASE_1 ": no data");
        }
        hexDumpWithHeader(PHASE_1, buffer, bytesRead);
        ++phaseIndex;
    }

    // protocol step: wait for pairing request from the device
    receive("pairing request", 64);

    // protocol step: send a pairing confirmation to the device
    send("pairing confirmation", buildAssociationResponse(buffer));

    // protocol step: wait for config info (as a response to pairing confirm)
    receive("config info");
    updateInvokeId();

    uint16_t pmStoreHandle = 0;
    uint16_t nbSegments = 0;
    if(false==parseConfigInfo(buffer, received, pmStoreHandle, nbSegments)) {
        fail(kExitProtocol, "failed to parse config info");
    }

    // protocol step: send "config well received" response
    send("config received confirmation", buildConfigReceived(buffer, invokeId));

    // protocol step: send MDS attribute request
    send("MDS attribute request", buildMdsRequest(buffer, invokeId));

    // protocol step: read MDS attr answer: identity and clock of the meter
    receive("MDS attribute answer");
    updateInvokeId();
    // read once: the offset and the time sent to the meter use the same PC time
    report.pc = options.pcClock ? options.pcClock() : PcClock{0, false, false};
    if(options.setTime && report.pc.known) {
        struct tm local;
        localtime_r(&report.pc.now, &local);
        char now[32];
        strftime(now, sizeof(now), "%Y/%m/%d %H:%M:%S", &local);
        transport.note(std::string("args: --set-time --now \"") + now + "\"");
    }
    const auto &m = report.meter;
    report.hasMeter = parseMdsAnswer(buffer, received, report.meter) &&
        (!m.manufacturer.empty() || !m.model.empty() || !m.serial.empty() || m.hasClock);
    if(!report.hasMeter) {
        LOG_WRN("MDS attribute answer not understood, meter identity and clock unknown");
    } else if(m.hasClock && report.pc.known) {
        report.hasClockOffset = true;
        report.clockOffsetS = long(localEpoch(m.clock) - report.pc.now);
        LOG_NFO("meter clock %s, offset %+ld s", formatTime(m.clock).c_str(), report.clockOffsetS);
    }

    // protocol step: send action request
    send("action request", buildSegmentInfoRequest(buffer, invokeId, pmStoreHandle));

    // protocol step: read action request response: what each segment holds
    receive("action request response");
    updateInvokeId();
    std::vector<SegmentInfo> segments;
    if(!parseSegmentInfo(buffer, received, segments)) {
        LOG_WRN("segment info not understood, reading segment 0 only");
        segments.clear();
    }
    const SegmentInfo *glucoseInfo = 0;
    const SegmentInfo *mealInfo = 0;
    for(const auto &s : segments) {
        if(kSegmentGlucose==s.kind && 0==glucoseInfo) {
            glucoseInfo = &s;
        }
        if(kSegmentMeal==s.kind && 0==mealInfo) {
            mealInfo = &s;
        }
    }
    if(glucoseInfo && glucoseInfo->hasUsageCount) {
        report.glucose.announced = true;
        report.glucose.expected = glucoseInfo->usageCount;
    }
    if(mealInfo) {
        report.hasMealSegment = true;
        report.meal.announced = mealInfo->hasUsageCount;
        report.meal.expected = mealInfo->usageCount;
    }

    // protocol step: set the meter clock to the PC clock (tidepool does it here too)
    report.clockAction = decideClock(options, report);
    if(kClockSet==report.clockAction) {
        struct tm local;
        localtime_r(&report.pc.now, &local);
        send("set time request", buildSetTime(buffer, invokeId, local));
        receive("set time answer");
        updateInvokeId();
        Reader r(buffer, received, 8);
        if(kDATA_ADPU_RESPONSE_CONFIRMED_ACTION!=r.u16()) {
            LOG_WRN("meter refused to set its clock");
            report.clockAction = kClockRejected;
        }
    }

    // read one segment: trigger its transfer, then read and acknowledge its
    // data messages; parse decodes the message in buffer and gives the fields
    // to echo in the ACK
    auto transferSegment = [&](
        uint16_t instance,
        const char *requestName,
        const char *headersName,
        const char *dataName,
        const char *ackName,
        const std::function<void(uint32_t &, uint32_t &, uint16_t &, bool &)> &parse
    ) {
        send(requestName, buildTriggerTransfer(buffer, invokeId, pmStoreHandle, instance));

        auto empty = false;
        {
            auto bytesRead = receive(headersName);
            updateInvokeId();

            uint16_t dataResponse = 0;
            if(22<=bytesRead) {
                size_t o = 20;
                dataResponse = be16r(buffer, o);
            }
            if(22==bytesRead && kDATA_RESPONSE_EMPTY==dataResponse) {
                LOG_NFO("segment %d holds no entry", (int)instance);
                empty = true;
            } else if(22==bytesRead && 0!=dataResponse) {
                fail(kExitProtocol, "error retrieving data, code = " + std::to_string(dataResponse));
            }

            uint16_t headerValue = -1;
            if(16<=bytesRead) {
                size_t o = 14;
                headerValue = be16r(buffer, o);
            }
            if(bytesRead<22 || kACTION_TYPE_MDC_ACT_SEG_TRIG_XFER!=headerValue) {
                fail(kExitProtocol, "unexpected / incorrect answer packet");
            }
        }

        // data messages carry the meter's own invoke ids, our next request
        // follows the answer to this one
        auto answerInvokeId = invokeId;
        size_t messages = 0;
        while(!empty) {
            if(messages==options.maxDataMessages) {
                fail(
                    kExitProtocol,
                    std::string("no last ") + dataName + " after " + std::to_string(messages) + " messages"
                );
            }
            ++messages;
            receive(dataName);
            updateInvokeId();
            uint32_t u0 = 0;
            uint32_t u1 = 0;
            uint16_t u2 = 0;
            bool last = false;
            parse(u0, u1, u2, last);
            send(ackName, buildSegmentAck(buffer, invokeId, pmStoreHandle, u0, u1, u2));

            // bail if segment was flagged as last one in the stream
            if(last) {
                break;
            }
        }
        invokeId = answerInvokeId;
    };

    // protocol step: glucose samples
    std::vector<Sample> samples;
    transferSegment(
        glucoseInfo ? glucoseInfo->instance : 0,
        "request segments",
        "segment headers",
        "data segment",
        "data segment received ACK",
        [&](uint32_t &u0, uint32_t &u1, uint16_t &u2, bool &last) {
            Segment segment;
            std::string error;
            if(!parseSegment(buffer, received, segment, error)) {
                fail(kExitProtocol, error);
            }
            for(const auto &s : segment.samples) {
                LOG_NFO(
                    "sample: %04d/%02d/%02d %02d:%02d => (mg/dL=%2d, mmol/L=%7.3f, status=0x%02x)",
                    s.year,
                    s.month,
                    s.day,
                    s.hour,
                    s.minute,
                    (int)s.value,
                    (s.value / 18.0),
                    (int)s.status
                );
                samples.push_back(s);
            }
            report.glucose.received = samples.size();
            u0 = segment.u0;
            u1 = segment.u1;
            u2 = segment.u2;
            last = segment.last;
        }
    );

    // protocol step: meal markers, kept in a segment of their own
    std::vector<MealEntry> meals;
    if(mealInfo && 0<mealInfo->usageCount) {
        transferSegment(
            mealInfo->instance,
            "request meal markers",
            "meal markers headers",
            "meal marker segment",
            "meal marker segment received ACK",
            [&](uint32_t &u0, uint32_t &u1, uint16_t &u2, bool &last) {
                MealSegment segment;
                std::string error;
                if(!parseMealSegment(buffer, received, segment, error)) {
                    fail(kExitProtocol, "meal markers: " + error);
                }
                meals.insert(meals.end(), segment.entries.begin(), segment.entries.end());
                report.meal.received = meals.size();
                u0 = segment.u0;
                u1 = segment.u1;
                u2 = segment.u2;
                last = segment.last;
            }
        );
    }
    report.mealsUnmatched = attachMeals(samples, meals);

    // protocol step: disconnect cleanly from device; every segment is acked by
    // now, so a failed release must not throw the samples away
    try {
        send("release request", buildReleaseRequest(buffer));
        receive("release confirmation");
    } catch(const SessionError &e) {
        LOG_WRN("release failed, samples kept: %s", e.what());
    }

    for(const auto &s : samples) {
        onSample(s);
    }
}

void downloadSamples(
    Transport &transport,
    const std::function<void(const Sample &)> &onSample
) {
    SessionReport report;
    downloadSamples(transport, SessionOptions(), report, onSample);
}

} // namespace accuchek
