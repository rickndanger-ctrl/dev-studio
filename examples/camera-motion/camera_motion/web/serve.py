"""Serve the camera-motion viewer on 127.0.0.1.

The catalog, frames.json and page bytes are loaded into memory at startup, so nothing is
written to disk and the installed package folder can stay read-only.

Usage: python3 -m camera_motion.web.serve [port]   (default port 8790)
"""
import sys

from camera_motion.web.server import Viewer, make_server

DEFAULT_PORT = 8790


def serve(port=DEFAULT_PORT):
    """Serve the viewer on 127.0.0.1:port until interrupted."""
    httpd = make_server(Viewer(), port)
    print(f"serving at http://127.0.0.1:{httpd.server_address[1]}/", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    port = int(argv[0]) if argv else DEFAULT_PORT
    return serve(port)


if __name__ == "__main__":
    sys.exit(main())
