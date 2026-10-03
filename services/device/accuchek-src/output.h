/*

    the JSON object accuchek prints on stdout (format 2, see
    schema/output.schema.json), built in memory so that it is written in one
    go, or not at all

 */

#ifndef __OUTPUT_H__
    #define __OUTPUT_H__

    #include <session.h>
    #include <string>
    #include <vector>

    namespace accuchek {

    std::string outputJson(const SessionReport &report, const std::vector<Sample> &samples);

    // one line per segment whose received count differs from the count the
    // meter announced: the download succeeded but may be incomplete
    std::vector<std::string> countWarnings(const SessionReport &report);

    } // namespace accuchek

#endif // __OUTPUT_H__
