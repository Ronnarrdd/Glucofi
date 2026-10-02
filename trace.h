/*

    recorded USB exchanges ("traces"), one transfer per line:

        c HEX     answer to the GET_STATUS control transfer
        < HEX     bulk transfer from the device
        > HEX     bulk transfer to the device
        < !CODE   transfer failed with transport error CODE (same for c and >)
        # ...     comment

    --capture writes one while talking to a real device, --replay plays one
    back instead of opening USB (sent messages must match the trace).

 */

#ifndef __TRACE_H__
    #define __TRACE_H__

    #include <session.h>
    #include <string>
    #include <vector>
    #include <stdio.h>

    namespace accuchek {

    static constexpr int kReplayMismatch = -1000;
    static constexpr int kReplayExhausted = -1001;

    struct ReplayTransport : Transport {

        struct Line {
            char kind;
            int error;
            std::vector<uint8_t> bytes;
        };

        // throws std::runtime_error on malformed text
        explicit ReplayTransport(const std::string &text);

        int controlStatus(uint8_t *buffer, size_t len) override;
        int bulkOut(const uint8_t *buffer, size_t len) override;
        int bulkIn(uint8_t *buffer, size_t maxLen) override;
        const char *errorName(int code) override;

        bool finished() const { return next==lines.size(); }

        std::vector<Line> lines;
        size_t next = 0;
        std::string lastError;

    private:
        const Line *take(char kind);
    };

    struct RecordingTransport : Transport {

        RecordingTransport(Transport &inner, FILE *out);

        int controlStatus(uint8_t *buffer, size_t len) override;
        int bulkOut(const uint8_t *buffer, size_t len) override;
        int bulkIn(uint8_t *buffer, size_t maxLen) override;
        const char *errorName(int code) override { return inner.errorName(code); }
        void note(const std::string &text) override;

    private:
        void record(char kind, const uint8_t *buffer, int result);
        Transport &inner;
        FILE *out;
    };

    std::string toHex(const uint8_t *buffer, size_t len);
    bool readFile(const char *path, std::string &text);

    } // namespace accuchek

#endif // __TRACE_H__
