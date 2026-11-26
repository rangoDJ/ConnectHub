import glob
import os
import sys

# The Selkies web client (selkies-core*.js), minified.
#
# Selkies 2.2 rewrote clipboard-sync and absorbed most of what ConnectHub patched in here:
# the client now reads the local clipboard on Ctrl+V itself (its keyboard gestures take a
# readLocalClipboard), prefers a copy's markup over the picture Office offers beside it, and
# puts chunked and single transfers down one path. Those patches were dropped rather than
# ported; see git history for what they did.
CLIENT_REPLACEMENTS = [
    # Fall back to plain text when the binary read is refused, not only when it fails to
    # decode. Chromium raises NotAllowedError for a read taken while the document is not
    # focused, where readText() can still answer; upstream retries on DataError alone.
    # Only the first occurrence is replaced: the catch around navigator.clipboard.read().
    (
        'catch(e){if(e&&e.name===`DataError`)return t();throw e}',
        'catch(e){if(e&&(e.name===`DataError`||e.name===`NotAllowedError`))return t();throw e}'
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
    # 3. Don't let a browser hand the session's own copy back over it. A copy with
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
    # 4. The comparison, beside the envelope helpers it uses
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
