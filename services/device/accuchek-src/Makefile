.PHONY: all clean test fuzz schema-check install uninstall hooks
SHELL = /bin/bash
CXX = g++ -std=c++17
LIBS = -lusb-1.0 -lm
# portable by default; for a binary tuned to this machine: make OPTFLAGS="-O3 -march=native"
OPTFLAGS ?= -O2
# the code builds without a single warning; CI and the pre-commit hook add WERROR=-Werror to keep it so
WARNINGS = -Wall -Wextra -Wshadow
WERROR ?=
CFLAGS = -g0 $(OPTFLAGS) -fomit-frame-pointer -DNDEBUG $(WARNINGS) $(WERROR)
# version: git describe in a clone of this repository, the VERSION file in a
# copy of the sources (tarball, /tmp build, sources vendored in another repo,
# whose tags are not ours)
GIT_VERSION := $(shell [ "$$(git rev-parse --show-toplevel 2>/dev/null)" = "$(CURDIR)" ] && git describe --tags --dirty --match 'v[0-9]*' 2>/dev/null)
VERSION := $(if $(GIT_VERSION),$(patsubst v%,%,$(GIT_VERSION)),$(shell cat VERSION))
PREFIX ?= /usr/local
UDEVDIR ?= /etc/udev/rules.d
# sanitizers when installed (libasan-devel, libubsan-devel)
SANITIZE := $(shell echo 'int main(){}' | $(CXX) -x c++ -fsanitize=address,undefined - -o /dev/null 2>/dev/null \
    && echo -fsanitize=address,undefined -fno-sanitize-recover=all)
TEST_CFLAGS = -O0 -g -fno-omit-frame-pointer -D_GLIBCXX_ASSERTIONS $(SANITIZE) $(WARNINGS) $(WERROR)

LIB_SRCS = protocol.cpp session.cpp trace.cpp output.cpp log.cpp
TEST_SRCS = tests/check.cpp tests/fuzz.cpp tests/test_protocol.cpp tests/test_session.cpp tests/test_bounds.cpp tests/test_output.cpp
FUZZ_SRCS = tests/fuzz.cpp tests/fuzz_main.cpp

all: accuchek
	@echo done.

accuchek: .objs/main.o $(LIB_SRCS:%.cpp=.objs/%.o)
	@echo lnk -- $@
	@$(CXX) $(CFLAGS) -o $@ $^ $(LIBS)

# main.o is rebuilt when the version changes, not on every make
.objs/version: FORCE
	@mkdir -p .objs
	@[ "$$(cat $@ 2>/dev/null)" = "$(VERSION)" ] || echo "$(VERSION)" > $@
.objs/main.o: .objs/version
.objs/main.o: CFLAGS += -DACCUCHEK_VERSION='"$(VERSION)"'
FORCE:

.objs/%.o: %.cpp Makefile
	@echo c++ -- $<
	@mkdir -p $(dir $@) .deps/$(dir $<)
	@$(CXX) -MMD -MF .deps/$*.d $(CFLAGS) -I. -c $< -o $@

# tests: built with sanitizers, run against the real binary for CLI checks
# -------------------------------------------------------------------------

.objs/test/%.o: %.cpp Makefile
	@echo c++ test -- $<
	@mkdir -p $(dir $@) .deps/test/$(dir $<)
	@$(CXX) -MMD -MF .deps/test/$*.d $(TEST_CFLAGS) -I. -c $< -o $@

.objs/test/run_tests: $(LIB_SRCS:%.cpp=.objs/test/%.o) $(TEST_SRCS:%.cpp=.objs/test/%.o)
	@echo lnk -- $@
	@$(CXX) $(TEST_CFLAGS) -o $@ $^ $(LIBS)

test: accuchek .objs/test/run_tests
	@[ -n "$(SANITIZE)" ] || echo "warning: tests built WITHOUT ASan/UBSan (install libasan and libubsan development packages)" >&2
	@ACCUCHEK_BIN="$(CURDIR)/accuchek" .objs/test/run_tests

# periodic eval: mutated packets against guard pages, FUZZ_ARGS="--seed N --count N"
.objs/test/fuzz: $(LIB_SRCS:%.cpp=.objs/test/%.o) $(FUZZ_SRCS:%.cpp=.objs/test/%.o)
	@echo lnk -- $@
	@$(CXX) $(TEST_CFLAGS) -o $@ $^ $(LIBS)

fuzz: .objs/test/fuzz
	@.objs/test/fuzz $(FUZZ_ARGS)

# replay every fixture and validate the JSON against the schema (pip install jsonschema)
schema-check: accuchek
	@ACCUCHEK_BIN="$(CURDIR)/accuchek" python3 tests/check_schema.py

# gate tests before every commit (hooks/pre-commit)
hooks:
	git config core.hooksPath hooks
	@echo "pre-commit hook active: make test runs before every commit"

# reload udev afterwards: udevadm control --reload && udevadm trigger --subsystem-match=usb
install: accuchek
	install -D -m 755 accuchek $(DESTDIR)$(PREFIX)/bin/accuchek
	install -D -m 644 udev/70-accuchek.rules $(DESTDIR)$(UDEVDIR)/70-accuchek.rules

uninstall:
	rm -f $(DESTDIR)$(PREFIX)/bin/accuchek $(DESTDIR)$(UDEVDIR)/70-accuchek.rules

clean:
	rm -r -f accuchek
	rm -r -f .deps .objs

-include $(shell find .deps -name '*.d' 2>/dev/null)
