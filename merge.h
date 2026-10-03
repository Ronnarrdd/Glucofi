/*

    --merge: a download added to a previous accuchek output (the archive)

    a meter keeps its last 720 readings; merging each download into an
    archive keeps the older ones too. The output stays format 2: meter, clock
    and counters describe this download, readings hold the archive readings
    the meter no longer has, then the download

 */

#ifndef __MERGE_H__
    #define __MERGE_H__

    #include <protocol.h>
    #include <string>
    #include <vector>

    namespace accuchek {

    struct Archive {
        std::string serial;             // meter serial, "" when unknown
        std::vector<Sample> samples;
    };

    // read an accuchek output: a format 2 object, or the plain readings array
    // of versions before 2.0; every reading is checked (key against
    // timestamp, range against mg/dL...), false with error set otherwise.
    // epoch, id and mmol/L are computed again on output; unknown fields are
    // ignored
    bool parseArchive(const std::string &text, Archive &archive, std::string &error);

    // the archive readings the download does not hold, in archive order,
    // then the download. Same reading: same key, mg/dL and status; an archive
    // reading without key (written before 2.2) is the download reading of
    // the same minute, mg/dL and status, matched one to one. Returns the
    // number of archive readings kept
    size_t mergeSamples(const std::vector<Sample> &archive, const std::vector<Sample> &download, std::vector<Sample> &merged);

    } // namespace accuchek

#endif // __MERGE_H__
