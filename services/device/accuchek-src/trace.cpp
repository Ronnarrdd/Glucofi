#include <trace.h>
#include <stdlib.h>
#include <string.h>
#include <algorithm>
#include <stdexcept>
#include <libusb-1.0/libusb.h>

namespace accuchek {

std::string toHex(
    const uint8_t *buffer,
    size_t len
) {
    static const char digits[] = "0123456789ABCDEF";
    std::string hex;
    hex.reserve(2*len);
    for(size_t i=0; i<len; ++i) {
        hex += digits[buffer[i] >> 4];
        hex += digits[buffer[i] & 0xF];
    }
    return hex;
}

bool readFile(
    const char *path,
    std::string &text
) {
    auto fp = fopen(path, "rb");
    if(0==fp) {
        return false;
    }
    char chunk[4096];
    size_t n;
    while(0<(n = fread(chunk, 1, sizeof(chunk), fp))) {
        text.append(chunk, n);
    }
    auto ok = (0==ferror(fp));
    fclose(fp);
    return ok;
}

static int hexValue(
    char c
) {
    if('0'<=c && c<='9') return c - '0';
    if('a'<=c && c<='f') return c - 'a' + 10;
    if('A'<=c && c<='F') return c - 'A' + 10;
    return -1;
}

ReplayTransport::ReplayTransport(
    const std::string &text
) {
    size_t start = 0;
    int lineNumber = 0;
    while(start<text.size()) {
        auto nl = text.find('\n', start);
        auto line = text.substr(start, (std::string::npos==nl ? std::string::npos : nl - start));
        start = (std::string::npos==nl ? text.size() : nl + 1);
        ++lineNumber;

        auto first = line.find_first_not_of(" \t\r");
        if(std::string::npos==first || '#'==line[first]) {
            continue;
        }

        auto bad = [&](const char *why) {
            throw std::runtime_error("trace line " + std::to_string(lineNumber) + ": " + why);
        };

        Line l;
        l.kind = line[first];
        l.error = 0;
        if('c'!=l.kind && '<'!=l.kind && '>'!=l.kind) {
            bad("unknown transfer kind");
        }
        auto payload = line.substr(first + 1);
        auto from = payload.find_first_not_of(" \t");
        auto to = payload.find_last_not_of(" \t\r");
        payload = (std::string::npos==from ? "" : payload.substr(from, to - from + 1));

        if(!payload.empty() && '!'==payload[0]) {
            char *end = 0;
            l.error = (int)strtol(payload.c_str() + 1, &end, 10);
            if(0<=l.error || '\0'!=*end) {
                bad("error code must be a negative integer");
            }
        } else {
            std::string digits;
            for(auto c : payload) {
                if(' '!=c && '\t'!=c) {
                    digits += c;
                }
            }
            if(0!=(digits.size() % 2)) {
                bad("odd number of hex digits");
            }
            for(size_t i=0; i<digits.size(); i+=2) {
                auto hi = hexValue(digits[i]);
                auto lo = hexValue(digits[i+1]);
                if(hi<0 || lo<0) {
                    bad("invalid hex digit");
                }
                l.bytes.push_back(uint8_t(hi*16 + lo));
            }
        }
        lines.push_back(l);
    }
}

const ReplayTransport::Line *ReplayTransport::take(
    char kind
) {
    if(finished()) {
        lastError = "end of trace";
        return 0;
    }
    auto &l = lines[next];
    if(kind!=l.kind) {
        lastError = std::string("trace expects '") + l.kind + "' at transfer " +
            std::to_string(next) + ", got '" + kind + "'";
        return 0;
    }
    ++next;
    return &l;
}

int ReplayTransport::controlStatus(
    uint8_t *buffer,
    size_t len
) {
    auto l = take('c');
    if(0==l) return kReplayMismatch;
    if(l->error) return l->error;
    auto n = std::min(len, l->bytes.size());
    std::copy_n(l->bytes.begin(), n, buffer);
    return (int)n;
}

int ReplayTransport::bulkIn(
    uint8_t *buffer,
    size_t maxLen
) {
    auto l = take('<');
    if(0==l) return finished() ? kReplayExhausted : kReplayMismatch;
    if(l->error) return l->error;
    // an empty line has a null data(), and memcpy(dst, nullptr, 0) is undefined
    auto n = std::min(maxLen, l->bytes.size());
    std::copy_n(l->bytes.begin(), n, buffer);
    return (int)n;
}

int ReplayTransport::bulkOut(
    const uint8_t *buffer,
    size_t len
) {
    auto l = take('>');
    if(0==l) return kReplayMismatch;
    if(l->error) return l->error;
    if(l->bytes.size()!=len || !std::equal(l->bytes.begin(), l->bytes.end(), buffer)) {
        lastError = "sent " + toHex(buffer, len) + ", trace expects " + toHex(l->bytes.data(), l->bytes.size());
        return kReplayMismatch;
    }
    return (int)len;
}

const char *ReplayTransport::errorName(
    int code
) {
    if(kReplayMismatch==code || kReplayExhausted==code) {
        return lastError.c_str();
    }
    return libusb_strerror(code);
}

RecordingTransport::RecordingTransport(
    Transport &_inner,
    FILE *_out
)
    :   inner(_inner),
        out(_out)
{
    fprintf(out, "# accuchek trace v1\n");
}

void RecordingTransport::record(
    char kind,
    const uint8_t *buffer,
    int result
) {
    if(result<0) {
        fprintf(out, "%c !%d\n", kind, result);
    } else {
        fprintf(out, "%c %s\n", kind, toHex(buffer, result).c_str());
    }
    fflush(out);
}

void RecordingTransport::note(
    const std::string &text
) {
    fprintf(out, "# %s\n", text.c_str());
    fflush(out);
}

int RecordingTransport::controlStatus(
    uint8_t *buffer,
    size_t len
) {
    auto r = inner.controlStatus(buffer, len);
    record('c', buffer, r);
    return r;
}

int RecordingTransport::bulkOut(
    const uint8_t *buffer,
    size_t len
) {
    auto r = inner.bulkOut(buffer, len);
    record('>', buffer, r<0 ? r : (int)len);
    return r;
}

int RecordingTransport::bulkIn(
    uint8_t *buffer,
    size_t maxLen
) {
    auto r = inner.bulkIn(buffer, maxLen);
    record('<', buffer, r);
    return r;
}

} // namespace accuchek
