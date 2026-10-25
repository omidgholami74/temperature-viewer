#!/usr/bin/env bash
# Compile and run the Arduino firmware's pure logic on a PC.
# Needs only a C++17 compiler; no ESP32 core and no board.
set -euo pipefail

cd "$(dirname "$0")"

CXX="${CXX:-g++}"
OUT="${TMPDIR:-/tmp}/furnace_arduino_tests"

"${CXX}" -std=c++17 -Wall -Wextra -Werror -O2 \
  -I ../furnace_arduino \
  test_math.cpp -o "${OUT}"

"${OUT}"
