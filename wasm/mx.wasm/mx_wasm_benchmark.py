#
# Copyright (c) 2019, Oracle and/or its affiliates. All rights reserved.
# DO NOT ALTER OR REMOVE COPYRIGHT NOTICES OR THIS FILE HEADER.
#
# The Universal Permissive License (UPL), Version 1.0
#
# Subject to the condition set forth below, permission is hereby granted to any
# person obtaining a copy of this software, associated documentation and/or
# data (collectively the "Software"), free of charge and under any and all
# copyright rights in the Software, and any and all patent rights owned or
# freely licensable by each licensor hereunder covering either (i) the
# unmodified Software as contributed to or provided by such licensor, or (ii)
# the Larger Works (as defined below), to deal in both
#
# (a) the Software, and
#
# (b) any piece of software and/or hardware listed in the lrgrwrks.txt file if
# one is included with the Software each a "Larger Work" to which the Software
# is contributed by such licensors),
#
# without restriction, including without limitation the rights to copy, create
# derivative works of, display, perform, and distribute the Software and make,
# use, sell, offer for sale, import, export, have made, and have sold the
# Software and the Larger Work(s), and to sublicense the foregoing rights on
# either these or other terms.
#
# This license is subject to the following condition:
#
# The above copyright notice and either this complete permission notice or at a
# minimum a reference to the UPL must be included in all copies or substantial
# portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
#
import mx
import mx_benchmark

import os
import shutil
import stat
import tempfile
import zipfile

from mx_benchmark import JMHDistBenchmarkSuite
from mx_benchmark import add_bm_suite
from mx_benchmark import add_java_vm


_suite = mx.suite("wasm")


BENCHMARK_NAME_PREFIX = "-Dwasmbench.benchmarkName="
SUITE_NAME_SUFFIX = "BenchmarkSuite"
BENCHMARK_JAR = "wasm-benchmarkcases.jar"


node_dir = mx.get_env("NODE_DIR", None)


class WasmBenchmarkVm(mx_benchmark.OutputCapturingVm):
    """
    This is a special kind of Wasm VM that expects the benchmark suite to provide
    a JAR file that has each benchmark compiled to a native binary,
    a JS program that runs the Wasm benchmark (generated e.g. with Emscripten),
    and the set of files that are required by the GraalWasm test suite.
    These files must be organized in a predefined structure,
    so that the different VM implementations know where to look for them.

    If a Wasm benchmark suite consists of benchmarks in the category `c`,
    then the binaries of that benchmark must structured as follows:

    - For GraalWasm: bench/x/{*.wasm, *.init, *.result, *.wat}
    - For Node: bench/x/node/{*.wasm, *.js}
    - For native binaries: bench/x/native/*<platform-specific-binary-extension>

    Furthermore, these VMs expect that the benchmark suites that use them
    will provide a `-Dwasmbench.benchmarkName=<benchmark-name>` command-line flag,
    and the `CBenchmarkSuite` argument, where `<benchmark-name>` specifies a benchmark
    in the category `c`.
    """
    def name(self):
        return "wasm-benchmark"

    def post_process_command_line_args(self, args):
        return args

    def parse_jar_suite_benchmark(self, args):
        if "-cp" not in args:
            mx.abort("Suite must specify -cp.")
        classpath = args[args.index("-cp") + 1]
        delimiter = ";" if mx.is_windows() else ":"
        jars = classpath.split(delimiter)
        jar = next(iter([jar for jar in jars if jar.endswith(BENCHMARK_JAR)]), None)
        if jar is None:
            mx.abort("No " + BENCHMARK_JAR + " specified in the classpath.")

        suite = next(iter([arg for arg in args if arg.endswith(SUITE_NAME_SUFFIX)]), None)
        if suite is None:
            mx.abort("Suite must specify a flag that ends with " + SUITE_NAME_SUFFIX)
        else:
            suite = suite[:-len(SUITE_NAME_SUFFIX)].lower()

        benchmark = next(iter([arg for arg in args if arg.startswith(BENCHMARK_NAME_PREFIX)]), None)
        if benchmark is None:
            mx.abort("Suite must specify a flag that starts with " + BENCHMARK_NAME_PREFIX)
        else:
            benchmark = benchmark[len(BENCHMARK_NAME_PREFIX):]

        return jar, suite, benchmark

    def extract_jar_to_tempdir(self, jar, mode, suite, benchmark):
        tmp_dir = tempfile.mkdtemp()
        with zipfile.ZipFile(jar, "r") as z:
            for name in z.namelist():
                if name.startswith(os.path.join("bench", suite, mode, benchmark)):
                    z.extract(name, tmp_dir)
        return tmp_dir

    def rules(self, output, benchmarks, bmSuiteArgs):
        pass


class NodeWasmBenchmarkVm(WasmBenchmarkVm):
    def config_name(self):
        return "node"

    def run_vm(self, args, out=None, err=None, cwd=None, nonZeroIsFatal=False):
        if node_dir is None:
            mx.abort("Must set the NODE_DIR environment variable to point to Node's bin dir.")
        jar, suite, benchmark = self.parse_jar_suite_benchmark(args)
        tmp_dir = None
        try:
            mode = "node"
            tmp_dir = self.extract_jar_to_tempdir(jar, mode, suite, benchmark)
            node_cmd = os.path.join(node_dir, mode)
            node_cmd_line = [node_cmd, os.path.join(tmp_dir, "bench", suite, mode, benchmark + ".js")]
            mx.log("Running benchmark " + benchmark + " with node.")
            mx.run(node_cmd_line, cwd=tmp_dir, out=out, err=err, nonZeroIsFatal=nonZeroIsFatal)
        finally:
            if tmp_dir:
                shutil.rmtree(tmp_dir)
        return 0


class NativeWasmBenchmarkVm(WasmBenchmarkVm):
    def config_name(self):
        return "native"

    def run_vm(self, args, out=None, err=None, cwd=None, nonZeroIsFatal=False):
        jar, suite, benchmark = self.parse_jar_suite_benchmark(args)
        tmp_dir = None
        try:
            mode = "native"
            tmp_dir = self.extract_jar_to_tempdir(jar, mode, suite, benchmark)
            binary_path = os.path.join(tmp_dir, "bench", suite, mode, mx.exe_suffix(benchmark))
            os.chmod(binary_path, stat.S_IRUSR | stat.S_IXUSR)
            cmd_line = [binary_path]
            mx.log("Running benchmark " + benchmark + " natively.")
            mx.run(cmd_line, cwd=tmp_dir, out=out, err=err, nonZeroIsFatal=nonZeroIsFatal)
        finally:
            if tmp_dir:
                shutil.rmtree(tmp_dir)
        return 0


add_java_vm(NodeWasmBenchmarkVm(), suite=_suite, priority=1)
add_java_vm(NativeWasmBenchmarkVm(), suite=_suite, priority=1)


class WasmBenchmarkSuite(JMHDistBenchmarkSuite):
    def name(self):
        return "wasm"

    def group(self):
        return "wasm"

    def subgroup(self):
        return "truffle"


add_bm_suite(WasmBenchmarkSuite())
