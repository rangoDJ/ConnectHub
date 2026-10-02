"""scripts/patch_selkies.py against a stand-in for the minified Selkies bundle.

The stand-in is createClipboardGestures from selkies-web-core/lib/clipboard-sync.js
(the release still using getDeferredWriteInFlight), minified by hand with the same
names as the real bundle, so it contains every string the patch script looks for.
With Node installed the patched code is also run, since a patch can apply cleanly and
still throw in the browser.
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

# The key handler's locals: t=modHold, n=chord, r=writeInFlight, i=hold. They shadow
# ft's canSync (r) and canRead (i).
BUNDLE = (
    "async function rl(o){let t=async()=>{let e=await navigator.clipboard.readText().catch(()=>``);"
    "return e?{kind:`text`,text:e}:null};"
    "try{return await navigator.clipboard.read()}catch(e){if(e&&e.name===`DataError`)return t();throw e}}\n"

    "function ft({isChromium:e,clipboardSync:t,sendClipboardData:n,canSync:r,canRead:i,canWrite:a,"
    "binaryEnabled:o,getSendInFlight:s,getDeferredWriteInFlight:c}){"
    "function l(){let e=document.activeElement;return!!(e&&e.id!==`overlayInput`&&(e.tagName===`INPUT`"
    "||e.tagName===`TEXTAREA`||e.tagName===`SELECT`||e.isContentEditable))}"
    "let u=[],d=!1,f=1e4;"
    "function p(){d=!1;for(let e of u.splice(0))try{let t=new KeyboardEvent(e.type,e);"
    "Object.defineProperty(t,`__selkiesClipReplay`,{value:!0}),window.dispatchEvent(t)}catch{}}"
    "function m(){for(let e=u.length-1;e>=0;e--)u[e].type===`keydown`&&u.splice(e,1);p()}"
    "let pm=[`ControlLeft`,`ControlRight`,`MetaLeft`,`MetaRight`];"
    "function h(e){if(e.__selkiesClipReplay)return;"
    "let t=d&&e.type===`keyup`&&pm.includes(e.code);"
    "if(e.code!==`KeyV`&&!t)return;let n=(e.ctrlKey||e.metaKey)&&!e.altKey,r=c?c():null;"
    "let i=t||e.code===`KeyV`&&(n&&(s()||r)||d);if(!i)return;"
    "e.preventDefault(),e.stopImmediatePropagation(),u.push(e);"
    "if(!d){d=!0;let t=performance.now(),n=()=>{let e=[],r=s();r&&e.push(r);let i=c?c():null;"
    "if(i&&e.push(i),e.length===0){p();return}let a=f-(performance.now()-t);if(a<=0){m();return}"
    "Promise.race([Promise.all(e).then(()=>`settled`,()=>`failed`),"
    "new Promise(e=>setTimeout(()=>e(`timeout`),a))]).then(e=>{e===`settled`?n():m()})};n()}}"
    "function ee(e){}"
    "function te(e){if(!r()||!i()||l())return;let a=e.clipboardData;if(!a)return;"
    "let s=a.getData(`text/plain`);s&&n(s)}"
    "function g(){window.addEventListener(`keydown`,h,!0),window.addEventListener(`keyup`,h,!0),"
    "e||(window.addEventListener(`keydown`,ee,!0),window.addEventListener(`paste`,te,!0))}"
    "function ne(){window.removeEventListener(`keydown`,h,!0),window.removeEventListener(`keyup`,h,!0),"
    "e||(window.removeEventListener(`keydown`,ee,!0),window.removeEventListener(`paste`,te,!0))}"
    "return{wire:g,unwire:ne}}\n"

    # WebRTC call site; the test drives this instance
    "globalThis.G=ft({isChromium:H.isChromium,clipboardSync:{},sendClipboardData:()=>{},"
    "canSync:()=>!0,canRead:()=>!0,canWrite:()=>!0,binaryEnabled:()=>!0,"
    "getSendInFlight:()=>hr.getSendInFlight(),"
    "getDeferredWriteInFlight:()=>z.getInFlight()});async function yr(){}\n"
    # WebSocket call site, never run here
    "function wsInit(){ft({isChromium:!0,getSendInFlight:()=>null,"
    "getDeferredWriteInFlight:()=>vn.getInFlight()}).wire();let b=()=>{}}\n"
)

# Browser stand-ins and a local clipboard sender whose send the test settles by hand
PRELUDE = r"""
const listeners = {};
globalThis.window = {
    addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
    removeEventListener() {},
    dispatchEvent(ev) { for (const fn of listeners[ev.type] || []) { fn(ev); if (ev._stopped) break; } },
    isSecureContext: H.secure,
};
globalThis.document = { activeElement: H.activeElement };
// Node has its own read-only navigator; the clipboard-read permission state is the test's
Object.defineProperty(globalThis, "navigator", { configurable: true, value: {
    permissions: { query: async () => ({ state: H.permission }) },
} });
globalThis.KeyboardEvent = class {
    constructor(type, init = {}) {
        this.type = type;
        for (const k of ["code", "ctrlKey", "metaKey", "altKey", "repeat"]) this[k] = init[k];
    }
    preventDefault() { this.defaultPrevented = true; }
    stopImmediatePropagation() { this._stopped = true; }
};
let inflight = null, settle = null, reads = 0;
const hr = {
    getSendInFlight: () => inflight,
    readAndSend() {
        reads++;
        inflight = new Promise(r => { settle = r; }).then(() => { inflight = null; });
        return inflight;
    },
};
const On = hr, z = { getInFlight: () => null }, vn = z;
"""

DRIVER = r"""
const reached = [];
G.wire();
window.addEventListener("keydown", ev => reached.push(ev.code)); // the input stack
const result = { error: null };
(async () => {
    await new Promise(r => setTimeout(r, 0)); // the permission query has answered
    try {
        window.dispatchEvent(new KeyboardEvent("keydown", { code: "KeyV", ctrlKey: true }));
    } catch (e) {
        result.error = `${e.name}: ${e.message}`;
    }
    result.reads = reads;
    result.reachedWhileSending = reached.slice();
    if (settle) settle();
    for (let i = 0; i < 10; i++) await new Promise(r => setTimeout(r, 0));
    result.reachedAfterSend = reached.slice();
    console.log(JSON.stringify(result));
})();
"""

# Patch 3 as first shipped: reads r() and i() above the handler's own `let r`/`let i`
ORIGINAL_PATCH_3 = (
    "if(e.code!==`KeyV`&&!t)return;let n=(e.ctrlKey||e.metaKey)&&!e.altKey;"
    "if(e.type===`keydown`&&e.code===`KeyV`&&n&&!l()&&r()&&i()&&!s()&&R)try{R()}catch{}"
    "let r=c?c():null;"
)


def patched(tmp_path) -> str:
    target = tmp_path / "selkies-core-test.js"
    target.write_text(BUNDLE, encoding="utf-8")
    assert patch_selkies.patch_file(str(target))
    return target.read_text(encoding="utf-8")


def run(code: str, is_chromium=True, active_element=None, secure=True, permission="granted"):
    if not shutil.which("node"):
        pytest.skip("node not installed")
    hooks = {
        "isChromium": is_chromium,
        "activeElement": active_element or {"id": "overlayInput", "tagName": "TEXTAREA"},
        "secure": secure,
        "permission": permission,
    }
    source = f"const H = {json.dumps(hooks)};\n{PRELUDE}\n{code}\n{DRIVER}"
    out = subprocess.run(["node", "-e", source], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_every_pattern_matches_the_bundle(tmp_path):
    code = patched(tmp_path)
    assert "readAndSend:__chReadAndSend=null" in code
    assert "readAndSend:()=>hr.readAndSend()" in code
    assert "readAndSend:()=>On.readAndSend()" in code


def test_patching_twice_is_a_no_op(tmp_path):
    once = patched(tmp_path)
    target = tmp_path / "selkies-core-test.js"
    assert patch_selkies.patch_file(str(target))
    assert target.read_text(encoding="utf-8") == once


def test_ctrl_v_reads_the_clipboard_and_holds_v_until_it_is_sent(tmp_path):
    result = run(patched(tmp_path))
    assert result["error"] is None
    assert result["reads"] == 1
    # V reaches the session only after the clipboard it pastes has
    assert result["reachedWhileSending"] == []
    assert result["reachedAfterSend"] == ["KeyV"]


def test_original_patch_threw_on_ctrl_v(tmp_path):
    """Pins the regression: the first version of patch 3 hit the TDZ of the key
    handler's own `r`, so every Ctrl+V threw and the paste hold never ran."""
    code = BUNDLE.replace(
        "if(e.code!==`KeyV`&&!t)return;let n=(e.ctrlKey||e.metaKey)&&!e.altKey,r=c?c():null;",
        ORIGINAL_PATCH_3,
    ).replace("getDeferredWriteInFlight:c}){", "getDeferredWriteInFlight:c,readAndSend:R=null}){")
    result = run(code)
    assert result["error"] is not None and "ReferenceError" in result["error"]
    assert result["reads"] == 0


def test_no_keydown_read_outside_chromium(tmp_path):
    """Firefox would raise its paste prompt; its paste event carries the clipboard."""
    result = run(patched(tmp_path), is_chromium=False)
    assert result["error"] is None
    assert result["reads"] == 0


def test_no_read_while_typing_in_a_page_form_field(tmp_path):
    result = run(patched(tmp_path), active_element={"id": "settings-name", "tagName": "INPUT"})
    assert result["error"] is None
    assert result["reads"] == 0


def test_no_keydown_read_without_https(tmp_path):
    """Without navigator.clipboard the read fails, and the hold it starts would cancel
    the keydown and so the paste event, the one path that carries the image there."""
    result = run(patched(tmp_path), secure=False)
    assert result["error"] is None
    assert result["reads"] == 0
    assert result["reachedWhileSending"] == ["KeyV"]


def test_no_keydown_read_when_clipboard_read_is_denied(tmp_path):
    result = run(patched(tmp_path), permission="denied")
    assert result["error"] is None
    assert result["reads"] == 0
    assert result["reachedWhileSending"] == ["KeyV"]


def test_first_use_still_reads_so_chromium_can_ask(tmp_path):
    result = run(patched(tmp_path), permission="prompt")
    assert result["reads"] == 1


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
    return json.dumps({m: d.decode() for m, d in entries}).encode()

logger_webrtc_input = logging.getLogger("input")


class Handler:
    async def write_clipboard(self, data, mime_type="text/plain", flavours=None):
        input_bytes = data if isinstance(data, bytes) else data.encode('utf-8')
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


# ----------------- Server: copies with text go as text -----------------

def read(server, offered, use_binary=True):
    return server._X11ClipboardMonitor(offered).read(use_binary)


def test_an_excel_copy_goes_as_its_text_and_markup_not_a_picture(server):
    """Excel offers a picture of the cells beside their text and HTML."""
    data, mime = read(server, {
        "image/png": b"\x89PNG...", "image/bmp": b"BM...",
        "text/html": b"<table><tr><td>1</td><td>2</td></tr></table>",
        "UTF8_STRING": b"1\t2\r\n",
    })
    assert mime == server.CLIPBOARD_FLAVOURS_MIME
    assert json.loads(data) == {"text/html": "<table><tr><td>1</td><td>2</td></tr></table>",
                                "text/plain": "1\t2\r\n"}


def test_plain_text_beside_a_picture_goes_as_text(server):
    assert read(server, {"image/bmp": b"BM...", "UTF8_STRING": b"cell"}) == ("cell", "text/plain")


def test_a_picture_only_copy_still_goes_as_an_image(server):
    """A screenshot or "Copy image": no text, so the picture is the copy."""
    assert read(server, {"image/png": b"PNGDATA", "text/html": b"<img src=x>"}) == (b"PNGDATA", "image/png")


def test_blank_text_beside_a_picture_does_not_count(server):
    assert read(server, {"image/png": b"PNGDATA", "UTF8_STRING": b" \r\n"}) == (b"PNGDATA", "image/png")


def test_text_only_copies_are_unchanged(server):
    assert read(server, {"UTF8_STRING": b"hello"}) == ("hello", "text/plain")
