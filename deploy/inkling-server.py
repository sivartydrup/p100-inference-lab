"""Start the pinned Inkling runner with a private authenticated HTTP listener."""

import argparse
import base64
from http.server import ThreadingHTTPServer
from pathlib import Path
import hmac
import os
import runpy
import signal
import stat
import subprocess
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("socket", type=Path)
    args = parser.parse_args()
    home = Path.home()
    source = home / "inference/scream-inkling2/engine-inkling2"
    data = home / "inference/models/inkling-2-mini-nvfp4"
    key_file = home / ".config/p100-gateway/inkling-key"
    key_info = key_file.lstat()
    if not stat.S_ISREG(key_info.st_mode) or stat.S_IMODE(key_info.st_mode) != 0o600:
        raise ValueError("Inkling requires a private key file")
    key = key_file.read_text(encoding="ascii").strip().encode("ascii")
    if not 32 <= len(key) <= 512 or not key.decode("ascii").isalnum():
        raise ValueError("Invalid Inkling key")
    expected_auth = b"Bearer " + key

    build = source / "build"
    llama = build / "llama.cpp"
    model = data / "Inkling-2-Mini-NVFP4-Q8_0.gguf"
    projector = data / "mmproj-Inkling-2-Mini-BF16.gguf"
    context = "262144"
    server_path = source / "serve/ink2_server.py"
    sys.argv = [str(value) for value in (server_path, "--host", "127.0.0.1", "--port", 8086,
        "--alias", "inkling2-mini", "--sock", args.socket, "--ctx", context, "--model", model,
        "--tokenizer", llama / "build/bin/llama-tokenize", "--gguf-py", llama / "gguf-py",
        "--mmproj", projector, "--vis", build / "ink2_vis")]
    os.environ["INK2_IMG_DIR"] = str(args.socket.parent / "images")
    upstream = runpy.run_path(str(server_path))

    def inline_image(source):
        metadata, separator, content = source.partition(",")
        if not separator or metadata not in {
            "data:image/png;base64", "data:image/jpeg;base64", "data:image/webp;base64",
        }:
            raise ValueError("Send images as inline PNG, JPEG, or WebP data URLs")
        return base64.b64decode(content, validate=True)

    # The upstream standalone server also accepts URLs and host file paths.
    upstream["encode_image"].__globals__["_image_bytes"] = inline_image

    class Handler(upstream["H"]):
        def authorized(self):
            header = self.headers.get("Authorization", "").encode("latin-1")
            if len(header) <= 519 and hmac.compare_digest(header, expected_auth):
                return True
            self.close_connection = True
            self._send(401, {"error": {"message": "A valid inference key is required."}})
            return False

        def do_GET(self):
            if self.authorized():
                super().do_GET()

        def do_POST(self):
            if self.authorized():
                super().do_POST()

    class Server(ThreadingHTTPServer):
        def service_actions(self):
            if engine.poll() is not None:
                raise RuntimeError("Inkling engine exited")

    def stop(*_):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    # Bind before allocating GPU memory so a conflicting listener fails early.
    with Server(("127.0.0.1", 8086), Handler) as server:
        engine = subprocess.Popen([build / "ink2_v1", "--model", model, "--mtp",
            data / "dspark-Inkling-2-Mini-Q8_0.gguf", "--draft-vocab",
            source / "data/draft_vocab_32768.bin", "--serve", args.socket, "--ctx", context])
        try:
            deadline = time.monotonic() + 180
            while not args.socket.is_socket():
                if engine.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError("Inkling did not become ready")
                time.sleep(0.25)
            print("Inkling ready at 127.0.0.1:8086 with 262144 context", flush=True)
            server.serve_forever()
        finally:
            if engine.poll() is None:
                engine.terminate()
                try:
                    engine.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    engine.kill()
                    engine.wait()


if __name__ == "__main__":
    main()
