import glob
import os
import sys

def patch_file(filepath):
    print(f"Inspecting {filepath}...")
    with open(filepath, "r", encoding="utf-8") as f:
        code = f.read()

    original_len = len(code)
    replacements = [
        # 1. Fallback to text if binary read throws NotAllowedError on focus
        (
            'catch(e){if(e&&e.name===`DataError`)return t();throw e}',
            'catch(e){if(e&&(e.name===`DataError`||e.name===`NotAllowedError`))return t();throw e}'
        ),
        # 2. Add readAndSend parameter to ft
        (
            'function ft({isChromium:e,clipboardSync:t,sendClipboardData:n,canSync:r,canRead:i,canWrite:a,binaryEnabled:o,getSendInFlight:s,getDeferredWriteInFlight:c}){',
            'function ft({isChromium:e,clipboardSync:t,sendClipboardData:n,canSync:r,canRead:i,canWrite:a,binaryEnabled:o,getSendInFlight:s,getDeferredWriteInFlight:c,readAndSend:R=null}){'
        ),
        # 3. Trigger read on KeyV keydown during user gesture
        (
            'if(e.code!==`KeyV`&&!t)return;let n=(e.ctrlKey||e.metaKey)&&!e.altKey,r=c?c():null;',
            'if(e.code!==`KeyV`&&!t)return;let n=(e.ctrlKey||e.metaKey)&&!e.altKey;if(e.type===`keydown`&&e.code===`KeyV`&&n&&!l()&&r()&&i()&&!s()&&R)try{R()}catch{}let r=c?c():null;'
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
    ]

    applied = 0
    for old, new in replacements:
        if old in code:
            code = code.replace(old, new, 1)
            applied += 1
        elif new in code:
            print("Notice: replacement already applied.")
            applied += 1
        else:
            print(f"Warning: pattern not found: {old[:60]}...")

    if applied == len(replacements):
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(code)
        print(f"Successfully applied all {applied} patches ({original_len} -> {len(code)} bytes).")
        return True
    else:
        print(f"Only {applied}/{len(replacements)} patches matched.")
        return False

if __name__ == "__main__":
    target_pattern = sys.argv[1] if len(sys.argv) > 1 else "/usr/share/selkies/web/assets/selkies-core-*.js"
    matches = glob.glob(target_pattern)
    if not matches:
        print(f"No files matched pattern: {target_pattern}")
        sys.exit(1)
    for m in matches:
        if not patch_file(m):
            sys.exit(1)
