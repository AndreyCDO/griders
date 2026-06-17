CXX ?= g++
CXXFLAGS ?= -std=c++17 -O2 -Wall -Wextra -pedantic
LDFLAGS ?= -lcrypto -pthread
SOURCES := m1_server.cpp m1_util.inc m1_storage.inc m1_mexc.inc m1_bot_engine.inc m1_web.inc

all: m1_server

m1_server: $(SOURCES)
	$(CXX) $(CXXFLAGS) -o $@ $< $(LDFLAGS)

clean:
	rm -f m1_server
