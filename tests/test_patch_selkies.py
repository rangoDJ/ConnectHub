"""scripts/patch_selkies.py against stand-ins for the Selkies files it patches.

The client stand-in is readLocalClipboard from Selkies 2.2's selkies-core.js, verbatim,
so it holds the exact string the one remaining client patch looks for. With Node
installed the patched code is also run, since a patch can apply cleanly and still throw
in the browser.
"""
import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "patch_selkies.py"
_spec = importlib.util.spec_from_file_location("patch_selkies", SCRIPT)
patch_selkies = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(patch_selkies)

# readLocalClipboard (Yt) from Selkies 2.2's selkies-core.js, verbatim. The patch target
# appears twice in it; only the first, the catch around navigator.clipboard.read(), is
# meant to be replaced.
BUNDLE = (
    "async function Yt(e){let t=async()=>{let e=await navigator.clipboard.readText()"
    ".catch(()=>``);return e?{kind:`text`,text:e}:null};if(!e){let e=await navigator"
    ".clipboard.readText();return e?{kind:`text`,text:e}:null}let n;try{n=await navigator"
    ".clipboard.read()}catch(e){if(e&&e.name===`DataError`)return t();throw e}"
    "if(!n||n.length===0)return null;let r=n[0],i=r.types.find(e=>e.startsWith(`image/`));"
    "try{let e=r.types.includes(`text/html`)?await(await r.getType(`text/html`)).text():``;"
    "if(i&&!Jt(e).trim())return{kind:`image`,blob:await r.getType(i),mime:i};"
    "if(e)return{kind:`flavours`,html:e,text:r.types.includes(`text/plain`)?"
    "await(await r.getType(`text/plain`)).text():``};"
    "if(r.types.includes(`text/plain`)){let e=await(await r.getType(`text/plain`)).text();"
    "return e?{kind:`text`,text:e}:null}}catch(e){if(e&&e.name===`DataError`)return t();"
    "throw e}return null}\n"
)

# A clipboard whose binary read fails with the name the test picks, and whose text read
# answers. Jt strips markup in the real bundle; nothing here depends on how.
PRELUDE = r"""
function Jt(s) { return String(s || "").replace(/<[^>]*>/g, ""); }
// Node has its own read-only navigator, so it has to be replaced rather than assigned
Object.defineProperty(globalThis, "navigator", { configurable: true, value: {
    clipboard: {
        read: async () => { const e = new Error("refused"); e.name = H.readError; throw e; },
        readText: async () => H.text,
    },
} });
"""

DRIVER = r"""
Yt(true).then(
    result => console.log(JSON.stringify({ error: null, result })),
    e => console.log(JSON.stringify({ error: e.name, result: null })),
);
"""


def patched(tmp_path) -> str:
    target = tmp_path / "selkies-core-test.js"
    target.write_text(BUNDLE, encoding="utf-8")
    assert patch_selkies.patch_file(str(target))
    return target.read_text(encoding="utf-8")


def run(code: str, read_error: str, text: str = "from the clipboard"):
    if not shutil.which("node"):
        pytest.skip("node not installed")
    hooks = {"readError": read_error, "text": text}
    source = f"const H = {json.dumps(hooks)};\n{PRELUDE}\n{code}\n{DRIVER}"
    out = subprocess.run(["node", "-e", source], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_the_client_pattern_matches_the_bundle(tmp_path):
    assert "`NotAllowedError`" in patched(tmp_path)


def test_only_the_binary_read_catch_is_patched(tmp_path):
    """The target appears twice; the trailing catch is left as upstream wrote it."""
    code = patched(tmp_path)
    assert code.count("e.name===`NotAllowedError`") == 1
    assert code.count("catch(e){if(e&&e.name===`DataError`)return t();throw e}") == 1


def test_patching_twice_is_a_no_op(tmp_path):
    once = patched(tmp_path)
    target = tmp_path / "selkies-core-test.js"
    assert patch_selkies.patch_file(str(target))
    assert target.read_text(encoding="utf-8") == once


def test_a_refused_read_falls_back_to_text(tmp_path):
    """Chromium refuses a read taken while the document is not focused; readText still
    answers, so a copy is not lost just because focus moved."""
    result = run(patched(tmp_path), "NotAllowedError")
    assert result["error"] is None
    assert result["result"] == {"kind": "text", "text": "from the clipboard"}


def test_an_unpatched_refused_read_is_lost(tmp_path):
    """Pins what the patch is for: upstream rethrows, so the clipboard never arrives."""
    result = run(BUNDLE, "NotAllowedError")
    assert result["error"] == "NotAllowedError"
    assert result["result"] is None


def test_an_undecodable_read_still_falls_back(tmp_path):
    """Upstream's own DataError fallback keeps working."""
    result = run(patched(tmp_path), "DataError")
    assert result["error"] is None
    assert result["result"]["kind"] == "text"


def test_other_read_errors_still_propagate(tmp_path):
    """Only the two named failures fall back; anything else is a real error."""
    result = run(patched(tmp_path), "SecurityError")
    assert result["error"] == "SecurityError"


def test_empty_clipboard_reads_as_nothing(tmp_path):
    result = run(patched(tmp_path), "NotAllowedError", text="")
    assert result["error"] is None
    assert result["result"] is None

# ----------------- Server -----------------

# The parts of Selkies 2.0.0's input_handler.py the server patches touch, verbatim
# (_X11ClipboardMonitor.read is the real method; the rest of that class is stubbed)
SERVER_MODULE = '''import asyncio
import io
import json
import logging
import time
from PIL import Image

CLIPBOARD_FLAVOURS_MIME = "application/x-selkies-clipboard-flavours"


def clipboard_envelope(entries):
    return json.dumps({mime: data.decode("utf-8", "replace")
                       for mime, data in entries}).encode("utf-8")


def clipboard_flavours(payload):
    decoded = json.loads(payload.decode("utf-8"))
    if not isinstance(decoded, dict) or not decoded:
        raise ValueError("clipboard flavours must be a non-empty object")
    entries = [(mime, decoded[mime].encode("utf-8"))
               for mime in ("text/html", "text/plain")
               if isinstance(decoded.get(mime), str) and decoded[mime]]
    if not entries:
        raise ValueError(f"no usable clipboard flavour in {sorted(decoded)}")
    return entries

# Re-reads the outbound monitor gives one selection-change edge whose read came
# back empty, before treating the selection as genuinely empty.
_CLIPBOARD_REREAD_ATTEMPTS = 2

logger_webrtc_input = logging.getLogger("input")


class Handler:
    _clipboard_last_bytes = None

    async def write_clipboard(self, data, mime_type="text/plain", flavours=None):
        if not data:
            return True
        input_bytes = data if isinstance(data, bytes) else data.encode('utf-8')
        self._clipboard_last_bytes = input_bytes
        self._clipboard_self_write = input_bytes
        # `data`/`mime_type` stay the flavour the session reads back and echoes
        # against; `flavours` is everything the copy carried, offered together.
        entries = [(m, d if isinstance(d, bytes) else d.encode('utf-8'))
                   for m, d in (flavours or [(mime_type, input_bytes)])]

        return entries


class _X11ClipboardMonitor:
    def __init__(self, offered):
        # The X selection's targets, named by their mime instead of atoms
        self.offered = offered
        self._targets = "TARGETS"
        self._image_targets = [(m, m) for m in (
            'image/png', 'image/jpeg', 'image/bmp', 'image/webp', 'image/svg+xml',
            'image/svg')]
        self._html_atom = 'text/html'
        self._text_targets = [(t, t) for t in (
            'UTF8_STRING', 'text/plain;charset=utf-8', 'STRING')]
        self._uri_list_atom = 'text/uri-list'

    def _convert_and_wait(self, atom):
        if atom == self._targets:
            return list(self.offered), 32
        return (self.offered[atom], 8) if atom in self.offered else None

    def read(self, use_binary: bool) -> tuple:
        """Blocking read (call via executor): (data, mime) like read_clipboard —
        text as str with mime 'text/plain', markup with the text beneath it as
        one envelope under CLIPBOARD_FLAVOURS_MIME, images as bytes with their
        mime.

        Images come first where the caller takes them, since a copied picture
        offers markup of its own (an `img` tag pointing back at a page) that is
        worth less than the picture; a text selection carries no image target,
        so its markup wins over the plain text beneath it.
        """
        reply = self._convert_and_wait(self._targets)
        if not reply or reply[1] != 32:
            # A fresh owner (xclip mid-fork) may not serve requests for a moment
            # after the owner-change event; one short retry covers it.
            time.sleep(0.1)
            reply = self._convert_and_wait(self._targets)
            if not reply or reply[1] != 32:
                return None, None
        offered = set(reply[0])
        if use_binary:
            for atom, mime in self._image_targets:
                if atom in offered:
                    got = self._convert_and_wait(atom)
                    if got and got[0]:
                        return bytes(got[0]), mime
            if self._uri_list_atom in offered:
                got = self._convert_and_wait(self._uri_list_atom)
                if got and got[0]:
                    resolved = self._resolve_uri_list_image(bytes(got[0]))
                    if resolved is not None:
                        return resolved
        if self._html_atom in offered:
            got = self._convert_and_wait(self._html_atom)
            if got is not None and got[0]:
                html = bytes(got[0])
                plain = b''
                for atom, _name in self._text_targets:
                    if atom in offered:
                        beside = self._convert_and_wait(atom)
                        if beside is not None and beside[0]:
                            plain = bytes(beside[0])
                            break
                entries = [("text/html", html)] + ([("text/plain", plain)] if plain else [])
                return clipboard_envelope(entries), CLIPBOARD_FLAVOURS_MIME
        for atom, _name in self._text_targets:
            if atom in offered:
                got = self._convert_and_wait(atom)
                if got is not None and got[0] is not None:
                    return bytes(got[0]).decode('utf-8', errors='replace'), 'text/plain'
        return None, None
'''


@pytest.fixture
def server(tmp_path):
    pytest.importorskip("PIL")
    target = tmp_path / "input_handler.py"
    target.write_text(SERVER_MODULE, encoding="utf-8")
    assert patch_selkies.patch_file(str(target), patch_selkies.SERVER_REPLACEMENTS)
    spec = importlib.util.spec_from_file_location("patched_input_handler", target)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write(server, data, mime_type):
    import asyncio
    return asyncio.run(server.Handler().write_clipboard(data, mime_type))


def png(pixels):
    import io
    from PIL import Image
    im = Image.new("RGBA", (len(pixels), 1))
    im.putdata(pixels)
    out = io.BytesIO()
    im.save(out, "PNG")
    return out.getvalue()


def test_a_pasted_png_is_offered_as_bmp_too(server):
    import io
    from PIL import Image
    data = png([(255, 0, 0, 255), (0, 0, 0, 0)])
    entries = write(server, data, "image/png")
    assert [m for m, _ in entries] == ["image/png", "image/bmp"]
    assert entries[0][1] == data  # the original is still offered first, unchanged
    bmp = entries[1][1]
    assert bmp[:2] == b"BM"
    with Image.open(io.BytesIO(bmp)) as im:
        assert im.mode == "RGB" and im.size == (2, 1)
        # opaque pixels kept; transparent ones flattened onto white, not black
        assert [im.getpixel((x, 0)) for x in range(2)] == [(255, 0, 0), (255, 255, 255)]


def test_text_gets_no_bmp(server):
    assert [m for m, _ in write(server, "hello", "text/plain")] == ["text/plain"]


def test_an_undecodable_image_is_offered_as_it_came(server):
    assert [m for m, _ in write(server, b"not an image", "image/png")] == ["image/png"]


def test_patching_the_server_twice_is_a_no_op(tmp_path):
    target = tmp_path / "input_handler.py"
    target.write_text(SERVER_MODULE, encoding="utf-8")
    assert patch_selkies.patch_file(str(target), patch_selkies.SERVER_REPLACEMENTS)
    once = target.read_text(encoding="utf-8")
    assert patch_selkies.patch_file(str(target), patch_selkies.SERVER_REPLACEMENTS)
    assert target.read_text(encoding="utf-8") == once

# ----------------- Server: the browser's echo of the session's own copy -----------------

def receive(server, handler, data, mime_type="text/plain", flavours=None):
    """A clipboard write from a browser; the written entries, or True if skipped."""
    import asyncio
    return asyncio.run(handler.write_clipboard(data, mime_type, flavours))


def excel_copy(server):
    """What the session last sent for copied cells: markup plus text."""
    return server.clipboard_envelope([("text/html", b"<table><tr><td>1</td><td>2</td></tr></table>"),
                                      ("text/plain", b"1\t2\r\n")])


def test_the_browsers_echo_of_a_rich_copy_is_not_written(server):
    """Chrome hands the markup back sanitized, so only the text still matches."""
    handler = server.Handler()
    handler._clipboard_last_bytes = excel_copy(server)
    echo = [("text/html", b"<meta charset='utf-8'><table><tr><td>1</td><td>2</td></tr></table>"),
            ("text/plain", b"1\t2\n")]
    assert receive(server, handler, echo[0][1], "text/html", echo) is True
    assert handler._clipboard_last_bytes == excel_copy(server)  # the session's copy stands


def test_the_echo_of_plain_text_is_not_written(server):
    handler = server.Handler()
    handler._clipboard_last_bytes = b"hello\r\n"
    assert receive(server, handler, "hello") is True


def test_a_new_local_copy_is_written(server):
    handler = server.Handler()
    handler._clipboard_last_bytes = excel_copy(server)
    entries = receive(server, handler, "something else")
    assert entries == [("text/plain", b"something else")]
    assert handler._clipboard_last_bytes == b"something else"


def test_a_new_rich_local_copy_is_written(server):
    handler = server.Handler()
    handler._clipboard_last_bytes = b"old text"
    flavours = [("text/html", b"<b>new</b>"), ("text/plain", b"new")]
    entries = receive(server, handler, b"<b>new</b>", "text/html", flavours)
    assert [m for m, _ in entries] == ["text/html", "text/plain"]


def test_images_are_always_written(server):
    """Browsers re-encode images, so an identical-looking one can't be told apart."""
    handler = server.Handler()
    data = png([(0, 0, 0, 255)])
    handler._clipboard_last_bytes = data
    assert [m for m, _ in receive(server, handler, data, "image/png")] == ["image/png", "image/bmp"]


def test_text_that_starts_with_a_brace_is_compared_as_text(server):
    handler = server.Handler()
    handler._clipboard_last_bytes = b'{"a": 1}'
    assert receive(server, handler, '{"a": 1}') is True
    assert receive(server, handler, '{"a": 2}') == [("text/plain", b'{"a": 2}')]


def test_nothing_is_skipped_before_the_session_has_a_clipboard(server):
    handler = server.Handler()
    assert receive(server, handler, "first") == [("text/plain", b"first")]

