import glob
import os
import sys

# The Selkies web client (selkies-core*.js), minified
CLIENT_REPLACEMENTS = [
    # 1. Fallback to text if binary read throws NotAllowedError on focus
    (
        'catch(e){if(e&&e.name===`DataError`)return t();throw e}',
        'catch(e){if(e&&(e.name===`DataError`||e.name===`NotAllowedError`))return t();throw e}'
    ),
    # 2. Add a readAndSend parameter to ft (createClipboardGestures), and a gate for it.
    # The gate has to live here at ft's top level: inside the key handler the names
    # r and i are the handler's own locals (writeInFlight, hold), not canSync/canRead.
    # Chromium only -- elsewhere a keydown read raises a paste prompt (Firefox) or is
    # rejected (WebKit), and the paste event already carries the clipboard. Also skipped
    # without HTTPS (no navigator.clipboard) or with clipboard-read denied: the read then
    # fails, while the hold it starts cancels the keydown and with it the paste event
    # (patch 4), the one path that still carries the image there.
    (
        'function ft({isChromium:e,clipboardSync:t,sendClipboardData:n,canSync:r,canRead:i,canWrite:a,binaryEnabled:o,getSendInFlight:s,getDeferredWriteInFlight:c}){',
        'function ft({isChromium:e,clipboardSync:t,sendClipboardData:n,canSync:r,canRead:i,canWrite:a,binaryEnabled:o,getSendInFlight:s,getDeferredWriteInFlight:c,readAndSend:__chReadAndSend=null}){'
        'let __chReadPerm=`prompt`;'
        'try{navigator.permissions.query({name:`clipboard-read`}).then(p=>{__chReadPerm=p.state;p.onchange=()=>{__chReadPerm=p.state}},()=>{})}catch(_){}'
        'let __chPasteRead=()=>{if(!e||!__chReadAndSend||!window.isSecureContext||__chReadPerm===`denied`||!r()||!i()||s())return;'
        'let ae=document.activeElement;'
        'if(ae&&ae.id!==`overlayInput`&&(ae.tagName===`INPUT`||ae.tagName===`TEXTAREA`||ae.tagName===`SELECT`||ae.isContentEditable))return;'
        'try{let p=__chReadAndSend();p&&p.catch&&p.catch(()=>{})}catch(_){}};'
    ),
    # 3. Read the local clipboard on the Ctrl/Cmd+V keydown, inside the user gesture.
    # Inserted after the handler's `let` so it can't hit those bindings' TDZ, and
    # before the hold check, which then sees the new send in flight and holds the V
    # until what it pastes has reached the session.
    (
        'if(e.code!==`KeyV`&&!t)return;let n=(e.ctrlKey||e.metaKey)&&!e.altKey,r=c?c():null;',
        'if(e.code!==`KeyV`&&!t)return;let n=(e.ctrlKey||e.metaKey)&&!e.altKey,r=c?c():null;if(e.type===`keydown`&&e.code===`KeyV`&&n&&!e.repeat)__chPasteRead();'
    ),
    # 4. Unconditionally listen for paste events in g()
    (
        'function g(){window.addEventListener(`keydown`,h,!0),window.addEventListener(`keyup`,h,!0),e||(window.addEventListener(`keydown`,ee,!0),window.addEventListener(`paste`,te,!0))}',
        'function g(){window.addEventListener(`keydown`,h,!0),window.addEventListener(`keyup`,h,!0),(e||window.addEventListener(`keydown`,ee,!0)),window.addEventListener(`paste`,te,!0)}'
    ),
    # 5. Unconditionally remove paste listener in ne()
    (
        'function ne(){window.removeEventListener(`keydown`,h,!0),window.removeEventListener(`keyup`,h,!0),e||(window.removeEventListener(`keydown`,ee,!0),window.removeEventListener(`paste`,te,!0))}',
        'function ne(){window.removeEventListener(`keydown`,h,!0),window.removeEventListener(`keyup`,h,!0),(e||window.removeEventListener(`keydown`,ee,!0)),window.removeEventListener(`paste`,te,!0)}'
    ),
    # 6. Pass readAndSend in WebRTC ft initialization
    (
        'getDeferredWriteInFlight:()=>z.getInFlight()});async function yr()',
        'getDeferredWriteInFlight:()=>z.getInFlight(),readAndSend:()=>hr.readAndSend()});async function yr()'
    ),
    # 7. Pass readAndSend in WebSocket ft initialization
    (
        'getDeferredWriteInFlight:()=>vn.getInFlight()}).wire();let b=()=>{',
        'getDeferredWriteInFlight:()=>vn.getInFlight(),readAndSend:()=>On.readAndSend()}).wire();let b=()=>{'
    ),
    # 8. Send a local copy that carries real text as text, not as its picture. Office
    # apps add a picture of the selection beside its text, which Chromium exposes as
    # image/png, and the client took any image first: cells copied in a local Excel
    # pasted into the session as a picture. A picture-only copy still goes as an image.
    # (The server patch for read() does the same in the other direction.)
    (
        'let r=n[0],i=r.types.find(e=>e.startsWith(`image/`));try{if(i)return{kind:`image`,blob:await r.getType(i),mime:i};',
        'let r=n[0],i=r.types.find(e=>e.startsWith(`image/`));try{'
        'if(i&&r.types.includes(`text/plain`)&&(await(await r.getType(`text/plain`)).text()).trim())i=void 0;'
        'if(i)return{kind:`image`,blob:await r.getType(i),mime:i};'
    ),
    # 9. The same for the paste event, whose data offers the picture as a file
    (
        'function te(e){if(!r()||!i()||l())return;let t=e.clipboardData;if(!t)return;if(o()&&t.items)for(',
        'function te(e){if(!r()||!i()||l())return;let t=e.clipboardData;if(!t)return;'
        'if(o()&&t.items&&!(t.getData(`text/plain`)||``).trim())for('
    ),
    # 10. A copy with markup that arrives in chunks. The server splits anything over
    # 16 KB, and the chunked handler knew only text and images: the markup-and-text
    # bundle went down the image path, failed to decode as a picture, and nothing
    # reached the clipboard. Excel's HTML is over 16 KB for even a few cells, so most
    # Excel copies were lost. Handled here as the single-message path handles it.
    (
        'yn.finish().then(({result:n,hash:r,byteLength:i})=>{if(t===`text/plain`){',
        'yn.finish().then(({result:n,hash:r,byteLength:i})=>{'
        'if(t===Qe){let a=et(typeof n==`string`?new TextEncoder().encode(n):n),o=Je(i,r),s=_n.shouldSend(o,t);'
        '_n.resolveServer(a.text||a.html,null,t,o),window.postMessage(ut(a.text||a.html),window.location.origin),'
        '!e&&W&&s&&Xt&&vn.write(()=>nt(a),{onFailure:e=>console.error(`Could not copy session markup to local: `+e)});return}'
        'if(t===`text/plain`){'
    ),
]

# The Selkies server (selkies/input_handler.py, Selkies 2.0.0)
SERVER_REPLACEMENTS = [
    # 1. Offer a BMP beside every image a client pastes. FreeRDP's X11 client gives
    # Windows images as CF_DIB, and Ubuntu's build converts only image/bmp to it: its
    # debian/rules passes -DWITH_WINPR_UTILS_IMAGE_PNG=ON (and _JPEG, _WEBP), but the
    # option is WINPR_UTILS_IMAGE_PNG, so libwinpr is built without them. The PNG the
    # browser sends is then never offered to Windows. Converted in the executor, as a
    # large image takes a while and write_clipboard runs on the event loop.
    (
        "        entries = [(m, d if isinstance(d, bytes) else d.encode('utf-8'))\n"
        "                   for m, d in (flavours or [(mime_type, input_bytes)])]\n",
        "        entries = [(m, d if isinstance(d, bytes) else d.encode('utf-8'))\n"
        "                   for m, d in (flavours or [(mime_type, input_bytes)])]\n"
        "        # ConnectHub: FreeRDP takes images only as BMP (see _connecthub_bmp_beside)\n"
        "        entries += await asyncio.get_running_loop().run_in_executor(\n"
        "            None, _connecthub_bmp_beside, entries)\n"
    ),
    # 2. The conversion, defined once the module's imports and logger exist
    (
        '\nlogger_webrtc_input = logging.getLogger("input")\n',
        '\nlogger_webrtc_input = logging.getLogger("input")\n'
        '\n'
        '\n'
        'def _connecthub_bmp_beside(entries):\n'
        '    """ConnectHub: a BMP of the first image in a clipboard write, to offer beside it.\n'
        '\n'
        '    FreeRDP hands an image to Windows only when the X clipboard offers image/bmp.\n'
        '    Transparency is flattened onto white: most Windows apps ignore a 32-bit BMP\'s\n'
        '    alpha and would show transparent pixels as their (often black) color.\n'
        '    """\n'
        '    if any(mime == "image/bmp" for mime, _data in entries):\n'
        '        return []\n'
        '    for mime, data in entries:\n'
        '        if not mime.startswith("image/") or mime.startswith("image/svg"):\n'
        '            continue\n'
        '        try:\n'
        '            with Image.open(io.BytesIO(data)) as im:\n'
        '                if im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info:\n'
        '                    rgba = im.convert("RGBA")\n'
        '                    frame = Image.new("RGB", rgba.size, (255, 255, 255))\n'
        '                    frame.paste(rgba, mask=rgba.getchannel("A"))\n'
        '                else:\n'
        '                    frame = im.convert("RGB")\n'
        '            out = io.BytesIO()\n'
        '            frame.save(out, "BMP")\n'
        '            return [("image/bmp", out.getvalue())]\n'
        '        except Exception as e:\n'
        '            logger_webrtc_input.warning(f"ConnectHub: no BMP beside clipboard {mime}: {e}")\n'
        '            return []\n'
        '    return []\n'
    ),
    # 3. Send a copy that carries real text as its text and markup, not as a picture.
    # Excel, Word and other Office apps offer a picture of the selection beside its
    # text and HTML, and Selkies takes any offered image first, so copied cells reached
    # the browser as a PNG that pastes nowhere text is expected. A picture-only copy
    # (a screenshot, "Copy image") offers no text and still goes as an image.
    (
        "        offered = set(reply[0])\n"
        "        if use_binary:\n"
        "            for atom, mime in self._image_targets:\n",
        "        offered = set(reply[0])\n"
        "        # ConnectHub: with real text beside the image, the text is what was copied\n"
        "        if use_binary and any(atom in offered for atom, _mime in self._image_targets):\n"
        "            for atom, _name in self._text_targets:\n"
        "                if atom in offered:\n"
        "                    got = self._convert_and_wait(atom)\n"
        "                    if got is not None and got[0] and bytes(got[0]).strip():\n"
        "                        use_binary = False\n"
        "                    break\n"
        "        if use_binary:\n"
        "            for atom, mime in self._image_targets:\n"
    ),
    # 4. Don't let a browser hand the session's own copy back over it. A copy with
    # markup (Excel cells, Word or web text) reaches the browser as markup plus text,
    # and the browser later reads its clipboard back and sends it as a new copy: on
    # every window focus in Chromium, and on Ctrl+V with the read patched in above.
    # The client can't tell it's an echo, as it fingerprints the text it received but
    # the markup-and-text bundle it sends. Written to the session, the browser's
    # sanitized markup replaced the copying app's own formats -- Excel pasted values
    # instead of cells and formulas -- and cancelled the copy in that app.
    (
        "        input_bytes = data if isinstance(data, bytes) else data.encode('utf-8')\n"
        "        self._clipboard_last_bytes = input_bytes\n",
        "        input_bytes = data if isinstance(data, bytes) else data.encode('utf-8')\n"
        "        # ConnectHub: the session already holds this copy (see _connecthub_same_text)\n"
        "        if _connecthub_same_text(self._clipboard_last_bytes, mime_type, input_bytes, flavours):\n"
        "            logger_webrtc_input.debug(\"ConnectHub: incoming clipboard matches the session's; keeping the session's\")\n"
        "            return True\n"
        "        self._clipboard_last_bytes = input_bytes\n"
    ),
    # 5. The comparison, beside the envelope helpers it uses
    (
        "\n# Re-reads the outbound monitor gives one selection-change edge whose read came\n",
        "\n"
        "\n"
        "def _connecthub_same_text(last_bytes, mime_type, data, flavours):\n"
        '    """ConnectHub: whether a copy coming in from a browser carries the same plain\n'
        "    text as the session clipboard already holds (`last_bytes`, as last read from\n"
        '    or written to the session). Images are never matched: browsers re-encode them."""\n'
        "    def plain(entries):\n"
        '        return next((d for m, d in entries if m == "text/plain"), None)\n'
        "\n"
        "    def norm(text):\n"
        '        return text.replace(b"\\r\\n", b"\\n").rstrip()\n'
        "\n"
        "    if flavours:\n"
        "        incoming = plain(flavours)\n"
        '    elif mime_type == "text/plain":\n'
        "        incoming = data\n"
        "    else:\n"
        "        return False\n"
        "    current = last_bytes\n"
        '    if current and current[:1] == b"{":\n'
        "        try:\n"
        "            current = plain(clipboard_flavours(current))\n"
        "        except (ValueError, UnicodeDecodeError):\n"
        "            pass  # plain text that happens to start with a brace\n"
        "    if not incoming or not current or not norm(incoming):\n"
        "        return False\n"
        "    return norm(incoming) == norm(current)\n"
        "\n"
        "\n"
        "# Re-reads the outbound monitor gives one selection-change edge whose read came\n"
    ),
]


def patch_file(filepath, replacements=CLIENT_REPLACEMENTS):
    print(f"Inspecting {filepath}...")
    with open(filepath, "r", encoding="utf-8") as f:
        code = f.read()

    original_len = len(code)
    applied = 0
    for old, new in replacements:
        # Checked first: a replacement that only appends still contains its
        # pattern, so it would otherwise be applied a second time
        if new in code:
            print(f"Notice: replacement already applied: {old[:40]}...")
            applied += 1
        elif old in code:
            code = code.replace(old, new, 1)
            applied += 1
        else:
            print(f"Warning: pattern not found: {old[:60]}...")

    if applied == len(replacements):
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(code)
        print(f"Successfully applied all {applied} patches to {filepath} ({original_len} -> {len(code)} bytes).")
        return True
    else:
        print(f"Only {applied}/{len(replacements)} patches matched in {filepath}.")
        return False

def find_targets():
    if len(sys.argv) > 1:
        matches = glob.glob(sys.argv[1])
        if matches:
            return matches

    candidates = []
    search_roots = ["/usr/share/selkies", "/usr/local/share/selkies", "/usr/share", "/lsiopy"]
    for root_dir in search_roots:
        if os.path.isdir(root_dir):
            for root, dirs, files in os.walk(root_dir):
                for file in files:
                    if file.startswith("selkies-core") and file.endswith(".js"):
                        full_path = os.path.join(root, file)
                        if full_path not in candidates:
                            candidates.append(full_path)
            if candidates:
                break

    return candidates

def find_server_targets():
    candidates = []
    for root_dir in ["/lsiopy", "/usr/lib", "/usr/local/lib"]:
        if os.path.isdir(root_dir):
            for root, dirs, files in os.walk(root_dir):
                if os.path.basename(root) == "selkies" and "input_handler.py" in files:
                    candidates.append(os.path.join(root, "input_handler.py"))
            if candidates:
                break
    return candidates

if __name__ == "__main__":
    targets = find_targets()
    if not targets:
        print("No selkies-core*.js files found in search roots.")
        sys.exit(1)

    print(f"Found {len(targets)} target file(s): {targets}")
    for t in targets:
        if not patch_file(t):
            sys.exit(1)

    server_targets = find_server_targets()
    if not server_targets:
        print("No selkies/input_handler.py found in search roots.")
        sys.exit(1)

    print(f"Found {len(server_targets)} server file(s): {server_targets}")
    for t in server_targets:
        if not patch_file(t, SERVER_REPLACEMENTS):
            sys.exit(1)
