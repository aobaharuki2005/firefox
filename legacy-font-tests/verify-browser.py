# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Exercise layout, WebRender and Canvas with isolated Momiji profiles."""

import argparse
import base64
from functools import partial
import http.server
import hashlib
import json
from pathlib import Path
import shutil
import socket
import subprocess
import threading
import time


class Marionette:
    def __init__(self, port):
        self.socket = socket.create_connection(("127.0.0.1", port), timeout=30)
        self.buffer = b""
        self.sequence = 0
        self.read()

    def read(self):
        while b":" not in self.buffer:
            data = self.socket.recv(65536)
            if not data:
                raise EOFError("Marionette disconnected")
            self.buffer += data
        size, data = self.buffer.split(b":", 1)
        size = int(size)
        while len(data) < size:
            chunk = self.socket.recv(65536)
            if not chunk:
                raise EOFError("Marionette disconnected")
            data += chunk
        self.buffer = data[size:]
        return json.loads(data[:size])

    def call(self, name, params=None):
        self.sequence += 1
        data = json.dumps([0, self.sequence, name, params or {}]).encode()
        self.socket.sendall(str(len(data)).encode() + b":" + data)
        while True:
            reply = self.read()
            if reply[0] == 1 and reply[1] == self.sequence:
                if reply[2]:
                    raise RuntimeError(reply[2])
                result = reply[3]
                return result.get("value", result) if isinstance(result, dict) else result

    def script(self, script, args=None, asynchronous=False):
        return self.call(
            "WebDriver:ExecuteAsyncScript" if asynchronous else "WebDriver:ExecuteScript",
            {"script": script, "args": args or [], "scriptTimeout": 30000},
        )


PAGE = """<!doctype html><meta charset=utf-8>
<style>
@font-face { font-family: Variable; src:url(actual-browser-luo.woff2); font-weight:200 900 }
@font-face { font-family: Static200; src:url(instanced-200.woff2) }
@font-face { font-family: Trimmed200; src:url(seam-fixed-200.woff2) }
REF_FACES
html, body { margin:0; background:white }
#sample { width:224px; height:224px; box-sizing:border-box; padding:32px;
          font-size:128px; line-height:160px; color:black; background:white;
          -moz-osx-font-smoothing:grayscale }
</style><div id=sample>络</div>
<script>
function raster(family, weight, transformed=false, text="络", size=128) {
  const canvas = document.createElement("canvas");
  canvas.width=Math.max(224,size+96); canvas.height=canvas.width;
  const ctx=canvas.getContext("2d");
  ctx.fillStyle="white"; ctx.fillRect(0,0,canvas.width,canvas.height);
  ctx.fillStyle="black"; ctx.font=`${weight} ${size}px ${family}`;
  if (transformed) ctx.transform(0.8,0,0.15,0.8,0,0);
  ctx.fillText(text,32.25,size+32.5);
  return {png:canvas.toDataURL().split(",")[1],
          advance:ctx.measureText(text).width,
          pixels:ctx.getImageData(0,0,canvas.width,canvas.height).data};
}
function compare(a,b) {
  let changed=0, ink=0, color=0, maxDelta=0, pixelsOver2=0;
  for(let i=0;i<a.pixels.length;i+=4) {
    let delta=0;
    for(let c=0;c<4;c++) delta=Math.max(delta,Math.abs(a.pixels[i+c]-b.pixels[i+c]));
    if(delta) changed++;
    maxDelta=Math.max(maxDelta,delta);
    if(delta>2) pixelsOver2++;
    if(a.pixels[i]<255 || a.pixels[i+1]<255 || a.pixels[i+2]<255) ink++;
    if(a.pixels[i]!==a.pixels[i+1] || a.pixels[i+1]!==a.pixels[i+2]) color++;
  }
  return {changed,ink,color,maxDelta,pixelsOver2,
          advance:a.advance,referenceAdvance:b.advance,png:a.png,referencePNG:b.png};
}
</script>
"""


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def run_mode(args, root, url, mode, index):
    variation, outlines = {
        "off": (False, False),
        "variations": (True, False),
        "outlines": (False, True),
        "both": (True, True),
    }[mode]
    out = root / mode
    out.mkdir()
    profile = out / "profile"
    profile.mkdir()
    port = args.port + index
    prefs = {
        "marionette.port": port,
        "gfx.font_rendering.legacy_macos.variations": variation,
        "gfx.font_rendering.legacy_macos.outlines": outlines,
        "layout.css.font-variations.enabled": True,
        "browser.shell.checkDefaultBrowser": False,
        "browser.startup.homepage_override.mstone": "ignore",
        "browser.aboutwelcome.enabled": False,
        "browser.newtabpage.enabled": False,
        "datareporting.policy.dataSubmissionEnabled": False,
        "toolkit.telemetry.reportingpolicy.firstRun": False,
        "app.update.enabled": False,
    }
    (profile / "user.js").write_text(
        "".join(f"user_pref({json.dumps(k)}, {json.dumps(v)});\n" for k, v in prefs.items())
    )
    command = [
        str(args.browser), "-no-remote", "-profile", str(profile), "-marionette",
        "--remote-allow-system-access",
    ]
    if args.headless:
        command.append("-headless")
    with (out / "browser.log").open("w") as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        client = None
        try:
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline and process.poll() is None:
                try:
                    client = Marionette(port)
                    break
                except OSError:
                    time.sleep(0.2)
            if client is None:
                raise RuntimeError(f"Browser did not start; see {out / 'browser.log'}")
            client.call("WebDriver:NewSession", {"capabilities": {}})
            client.call("Marionette:SetContext", {"value": "chrome"})
            platform = client.script(
                """return {
                  buildID:Services.appinfo.appBuildID,
                  graphics:(()=>{
                    const info=Cc["@mozilla.org/gfx/info;1"].getService(Ci.nsIGfxInfo);
                    const data=info.getInfo();
                    const win=Services.wm.getMostRecentWindow("navigator:browser");
                    return {
                      canvas:data.AzureCanvasBackend,
                      content:data.AzureContentBackend,
                      compositor:win ? win.windowUtils.layerManagerType : null
                    };
                  })(),
                  preferences:Object.fromEntries([
                    "gfx.font_rendering.legacy_macos.variations",
                    "gfx.font_rendering.legacy_macos.outlines",
                    "layout.css.font-variations.enabled"].map(
                      p=>[p,Services.prefs.getBoolPref(p,false)]))
                };"""
            )
            client.call("Marionette:SetContext", {"value": "content"})
            client.call("WebDriver:Navigate", {"url": url})
            loaded = client.script(
                """const done=arguments[arguments.length-1];
                Promise.all(["Variable","Static200","Trimmed200",
                  "Ref200","Ref400","Ref500","Ref550","Ref700"].map(
                    f=>document.fonts.load(`128px ${f}`))).then(fonts=>
                  done(fonts.map(f=>f.length)));""",
                asynchronous=True,
            )
            assert all(loaded), loaded
            result = client.script(
                """const weights=[200,400,500,550,700];
                return {
                  userAgent:navigator.userAgent,
                  variableCSS:CSS.supports("font-variation-settings","'wght' 550"),
                  weights:weights.map(w=>({weight:w,
                    ...compare(raster("Variable",w),raster("Ref"+w,400))})),
                  overlap:compare(raster("Static200",400),raster("Trimmed200",400)),
                  transformed:compare(raster("Static200",400,true),
                    raster("Trimmed200",400,true)),
                  repeat:compare(raster("Variable",550),raster("Variable",550)),
                  sizes:[16,24,512].flatMap(size=>[
                    {size,kind:"weight400",...compare(
                      raster("Variable",400,false,"络",size),
                      raster("Ref400",400,false,"络",size))},
                    {size,kind:"weight700",...compare(
                      raster("Variable",700,false,"络",size),
                      raster("Ref700",400,false,"络",size))},
                    {size,kind:"overlap",...compare(
                      raster("Static200",400,false,"络",size),
                      raster("Trimmed200",400,false,"络",size))}
                  ]),
                  emoji:compare(raster('"Apple Color Emoji"',400,false,"😊"),
                    raster('"Apple Color Emoji"',400,false,"😊"))
                };"""
            )
            screenshots = {}
            for family, weight in [
                ("Static200", 400), ("Trimmed200", 400),
                ("Variable", 400), ("Ref400", 400),
                ("Variable", 700), ("Ref700", 400),
            ]:
                client.script(
                    """const [family,weight]=arguments;
                    const done=arguments[arguments.length-1];
                    const sample=document.querySelector("#sample");
                    sample.style.fontFamily=family; sample.style.fontWeight=weight;
                    requestAnimationFrame(()=>requestAnimationFrame(()=>done(true)));""",
                    [family, weight], asynchronous=True,
                )
                element = client.call("WebDriver:FindElement", {"using": "css selector", "value": "#sample"})
                element_id = element["element-6066-11e4-a52e-4f735466cecf"]
                png = client.call("WebDriver:TakeScreenshot", {"id": element_id, "full": False, "scroll": True})
                name = f"dom-{family}-{weight}.png"
                screenshots[name] = base64.b64decode(png)
                (out / name).write_bytes(screenshots[name])
            result["dom"] = {
                "overlap_matches": screenshots["dom-Static200-400.png"] == screenshots["dom-Trimmed200-400.png"],
                "weight400_matches": screenshots["dom-Variable-400.png"] == screenshots["dom-Ref400-400.png"],
                "weight700_matches": screenshots["dom-Variable-700.png"] == screenshots["dom-Ref700-400.png"],
            }
            result["platform"] = platform
            for entry in result["weights"]:
                png = base64.b64decode(entry.pop("png"))
                entry["sha256"] = hashlib.sha256(png).hexdigest()
                (out / f"canvas-variable-{entry['weight']}.png").write_bytes(png)
                (out / f"canvas-reference-{entry['weight']}.png").write_bytes(base64.b64decode(entry.pop("referencePNG")))
            for key in ["overlap", "transformed", "emoji", "repeat"]:
                (out / f"canvas-{key}.png").write_bytes(base64.b64decode(result[key].pop("png")))
                (out / f"canvas-{key}-reference.png").write_bytes(base64.b64decode(result[key].pop("referencePNG")))
            for entry in result["sizes"]:
                name = f"canvas-{entry['size']}-{entry['kind']}"
                (out / f"{name}.png").write_bytes(base64.b64decode(entry.pop("png")))
                (out / f"{name}-reference.png").write_bytes(base64.b64decode(entry.pop("referencePNG")))
            (out / "results.json").write_text(json.dumps(result, indent=2) + "\n")
            return result
        finally:
            if client:
                client.socket.close()
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", type=Path, required=True)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--instances", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--modes", nargs="+", default=["off", "variations", "outlines", "both"])
    parser.add_argument("--port", type=int, default=28380)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--assert-fixed", action="store_true")
    parser.add_argument("--max-aa-delta", type=int, default=0)
    args = parser.parse_args()
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=False)
    web = args.out / "web"
    web.mkdir()
    for name in ["actual-browser-luo.woff2", "instanced-200.woff2", "seam-fixed-200.woff2"]:
        shutil.copy2(args.fixtures / name, web / name)
    faces = []
    for weight in [200, 400, 500, 550, 700]:
        name = f"instance-{weight}.ttf"
        shutil.copy2(args.instances / name, web / name)
        faces.append(f'@font-face {{font-family:Ref{weight};src:url({name})}}')
    (web / "index.html").write_text(PAGE.replace("REF_FACES", "\n".join(faces)))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(web)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        results = {
            mode: run_mode(args, args.out, f"http://127.0.0.1:{server.server_port}/", mode, i)
            for i, mode in enumerate(args.modes)
        }
        (args.out / "results.json").write_text(json.dumps(results, indent=2) + "\n")
        print(json.dumps(results, indent=2))
        if args.assert_fixed:
            fixed = results["both"]
            assert fixed["variableCSS"]
            assert all(w["maxDelta"] <= args.max_aa_delta and w["advance"] == w["referenceAdvance"]
                       for w in fixed["weights"]), fixed["weights"]
            assert len({w["sha256"] for w in fixed["weights"]}) == 5
            assert fixed["overlap"]["maxDelta"] <= args.max_aa_delta
            assert fixed["transformed"]["maxDelta"] <= args.max_aa_delta
            assert fixed["repeat"]["changed"] == 0
            assert all(s["maxDelta"] <= args.max_aa_delta and s["advance"] == s["referenceAdvance"]
                       for s in fixed["sizes"]), fixed["sizes"]
            assert fixed["emoji"]["color"] > 0
            assert all(fixed["dom"].values()), fixed["dom"]
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
