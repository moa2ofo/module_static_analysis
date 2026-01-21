# syntax=docker/dockerfile:1

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        cmake \
        ninja-build \
        gcc \
        g++ \
        cppcheck \
        python3 \
        python3-pip \
        python3-setuptools \
        python3-wheel \
        python3-requests \
        python3-lxml \
        python3-pygments \
        git \
        ca-certificates \
        clang-format && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

RUN mkdir -p /opt/misra && \
    cat <<'EOF' > /opt/misra/misra.json
{
    "script": "misra.py",
    "args": [
        "--rule-texts=/workspace/misra/misra_c_2012_headlines.txt"
    ]
}
EOF

RUN cat <<'EOF' > /usr/local/bin/run-misra-check.sh
#!/usr/bin/env bash
set -euo pipefail

PROJ_DIR="${1:-$(pwd)}"

cd "${PROJ_DIR}"

CC_JSON="$(find . -maxdepth 5 -type f -name compile_commands.json | head -n 1 || true)"

if [[ -z "${CC_JSON}" ]]; then
  echo "ERROR: compile_commands.json not found under ${PROJ_DIR}" >&2
  echo "Hint: Run CMake with -DCMAKE_EXPORT_COMPILE_COMMANDS=ON" >&2
  exit 1
fi

if command -v nproc >/dev/null 2>&1; then
  CPPCHECK_JOBS="$(nproc)"
else
  CPPCHECK_JOBS=4
fi

echo "Running cppcheck with MISRA addon on ${CC_JSON} using ${CPPCHECK_JOBS} jobs..." >&2

cppcheck \
  -j "${CPPCHECK_JOBS}" \
  --project="${CC_JSON}" \
  --enable=style,warning,performance,portability \
  --inconclusive \
  --force \
  --inline-suppr \
  --addon=/opt/misra/misra.json \
  --error-exitcode=1 \
  --xml --xml-version=2 2> cppcheck_misra_results.xml

echo "MISRA analysis completed. XML saved to ${PROJ_DIR}/cppcheck_misra_results.xml" >&2
EOF
RUN chmod +x /usr/local/bin/run-misra-check.sh

RUN cat <<'EOF' > /usr/local/bin/build-and-check-all.sh
#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="/workspace/code"

if [[ ! -d "${ROOT_DIR}" ]]; then
  echo "ERROR: Expected root directory does not exist: ${ROOT_DIR}" >&2
  exit 1
fi

echo "Scanning for CMake projects under ${ROOT_DIR}..." >&2

mapfile -t PROJECTS < <(
  find "${ROOT_DIR}" \
    -type d \( -name build -o -name .git \) -prune -o \
    -type f -name CMakeLists.txt -print0 \
  | xargs -0 -n1 dirname \
  | sort -u
)


if [[ "${#PROJECTS[@]}" -eq 0 ]]; then
  echo "No CMake projects found under ${ROOT_DIR}" >&2
  exit 0
fi

for PROJ in "${PROJECTS[@]}"; do
  echo "============================================================" >&2
  echo "Project: ${PROJ}" >&2
  echo "============================================================" >&2

  BUILD_DIR="${PROJ}/build"

  rm -rf "${BUILD_DIR}"
  mkdir -p "${BUILD_DIR}"

  echo "Configuring CMake..." >&2
  cmake -S "${PROJ}" -B "${BUILD_DIR}" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_EXPORT_COMPILE_COMMANDS=ON

  echo "Building..." >&2
  cmake --build "${BUILD_DIR}" -- -j"$(nproc || echo 4)"

  echo "Running MISRA analysis..." >&2
  if ! run-misra-check.sh "${PROJ}"; then
    echo "⚠️ MISRA analysis FAILED in ${PROJ} (continuing)" >&2
  fi

  echo "Done with ${PROJ}" >&2
  echo
done

echo "All projects under ${ROOT_DIR} processed." >&2
EOF
RUN chmod +x /usr/local/bin/build-and-check-all.sh

RUN cat <<'EOF' > /usr/local/bin/run-clang-format-all.sh
#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="/workspace/code"

if [[ ! -d "${ROOT_DIR}" ]]; then
  echo "ERROR: Expected directory does not exist: ${ROOT_DIR}" >&2
  exit 1
fi

if [[ ! -f "${ROOT_DIR}/.clang-format" ]]; then
  cat <<'YAML' > "${ROOT_DIR}/.clang-format"
BasedOnStyle: LLVM
IndentWidth: 2
UseTab: Never
BreakBeforeBraces: Attach
AllowShortIfStatementsOnASingleLine: true
AllowShortBlocksOnASingleLine: true
AllowShortFunctionsOnASingleLine: All
AllowShortLoopsOnASingleLine: true
ColumnLimit: 200
SpaceBeforeParens: Never
YAML
fi

echo "Running clang-format recursively under ${ROOT_DIR}..." >&2

find "${ROOT_DIR}" -type f \( \
  -name '*.c' -o -name '*.h' -o -name '*.cpp' -o -name '*.hpp' -o \
  -name '*.cc' -o -name '*.hh' -o -name '*.cxx' -o -name '*.hxx' \
\) -print0 | xargs -0 clang-format -i

echo "clang-format completed." >&2
EOF
RUN chmod +x /usr/local/bin/run-clang-format-all.sh

RUN find /usr/local/bin -type f -exec sed -i 's/\r$//' {} \;

CMD ["run-clang-format-all.sh"]
