#!/usr/bin/env python2

import mistune
import lib.mdrenderer
import re
import codecs
import pybars
import yaml
import argparse
import sys
import os
import subprocess
from copy import deepcopy


class PcbDrawInlineLexer(mistune.InlineLexer):
    def __init__(self, renderer, rules=None, **kwargs):
        super(PcbDrawInlineLexer, self).__init__(renderer, rules=None, **kwargs)
        self.enable_pcbdraw()

    def enable_pcbdraw(self):
        self.rules.pcbdraw = re.compile(
            r"\[\["                   # [[
            r"([\s\S]+?\|[\s\S]+?)"   # side| component
            r"\]\](?!\])"             # ]]
        )
        self.default_rules.insert(3, "pcbdraw")

    def output_pcbdraw(self, m):
        text = m.group(1)
        side, components = text.split("|")
        components = list(map(lambda x: x.strip(), components.split(",")))
        return self.renderer.pcbdraw(side, components)

def Renderer(BaseRenderer):
    class Tmp(BaseRenderer):
        def __init__(self):
            super(Tmp, self).__init__(escape=False)
            self.items = []
            self.current_item = None
            self.active_side = "front"
            self.visited_components = []
            self.active_components = []

        def append_comment(self, html):
            if self.current_item is not None and self.current_item["type"] == "steps":
                self.items.append(self.current_item)
            if self.current_item is None or self.current_item["type"] == "steps":
                self.current_item = {
                    "is_comment": True,
                    "type": "comment",
                    "content": ""
                }
            self.current_item["content"] += html

        def append_step(self, step):
            if self.current_item is not None and self.current_item["type"] == "comment":
                self.items.append(self.current_item)
            if self.current_item is None or self.current_item["type"] == "comment":
                self.current_item = {
                    "is_step": True,
                    "type": "steps",
                    "steps": []
                }
            self.current_item["steps"].append(step)

        def output(self):
            items = self.items
            items.append(self.current_item)
            return items

        def pcbdraw(self, side, components):
            self.active_side = side
            self.visited_components += components
            self.active_components = components
            return ""

        def block_code(self, code, lang):
            retval = super(Tmp, self).block_code(code, lang)
            self.append_comment(retval)
            return retval

        def block_quote(self, text):
            retval = super(Tmp, self).block_quote(text)
            self.append_comment(retval)
            return retval

        def block_html(self, html):
            retval = super(Tmp, self).block_html(html)
            self.append_comment(retval)
            return retval

        def header(self, text, level, raw=None):
            retval = super(Tmp, self).header(text, level, raw)
            self.append_comment(retval)
            return retval

        def hrule(self):
            retval = super(Tmp, self).hrule()
            self.append_comment(retval)
            return retval

        def list(self, body, ordered=True):
            return ""

        def list_item(self, text):
            step = {
                "side": self.active_side,
                "components": self.visited_components,
                "active_components": self.active_components,
                "comment": text
            }
            self.append_step(deepcopy(step))
            return ""

        def paragraph(self, text):
            retval = super(Tmp, self).paragraph(text)
            self.append_comment(retval)
            return retval

        def table(self, header, body):
            retval = super(Tmp, self).table(header, body)
            self.append_comment(retval)
            return retval
    return Tmp()

def load_content(filename):
    header = None
    with codecs.open(filename, encoding="utf-8") as f:
        content = f.read()
        if content.startswith("---"):
            end = content.find("...")
            if end != -1:
                header = yaml.load(content[3:end])
                content = content[end+3:]
    return header, content

def parse_content(renderer, content):
    lexer = PcbDrawInlineLexer(renderer)
    processor = mistune.Markdown(renderer=renderer, inline=lexer)
    processor(content)
    return renderer.output()

def read_template(filename):
    with codecs.open(filename, encoding="utf-8") as f:
        return f.read()

def generate_html(template, input):
    input = {
        "items": input
    }
    template = pybars.Compiler().compile(template)
    return template(input).encode("utf-8")

def generate_markdown(input):
    output = ""
    for item in input:
        if item["type"] == "comment":
            output += item["content"] + "\n"
        else:
            for x in item["steps"]:
                output += "#### " + x["comment"] + "\n\n"
                output += "![step](" + x["img"] + ")\n\n"
    return output.encode("utf-8")

def flatten(l):
    return [item for sublist in l for item in sublist]

def generate_images(content, boardfilename, libs, parameters, name, outdir):
    dir = os.path.dirname(os.path.join(outdir, name))
    if not os.path.exists(dir):
        os.makedirs(dir)
    counter = 0
    for item in content:
        if item["type"] == "comment":
            continue
        for x in item["steps"]:
            counter += 1
            filename = name.format(counter)
            generate_image(boardfilename, libs, x["side"], x["components"],
                x["active_components"], parameters, os.path.join(outdir, filename))
            x["img"] = filename
    return content

def svg_to_png(infile, outfile, dpi=300):
    import cairo
    import gi
    gi.require_version('Rsvg', '2.0')
    from gi.repository import Rsvg

    handle = Rsvg.Handle()
    svg = handle.new_from_file(infile)
    svg.set_dpi(dpi)
    dim = svg.get_dimensions()
    w, h = dim.width, dim.height
    img =  cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    ctx = cairo.Context(img)
    svg.render_cairo(ctx)
    img.write_to_png(outfile)

def generate_image(boardfilename, libs, side, components, active, parameters, outputfile):
    svgfilename = os.path.splitext(outputfile)
    svgfilename, ext = svgfilename[0] + ".svg", svgfilename[1]

    command = ["./pcbdraw.py", "-f", ",".join(components), "-a", ",".join(active)]
    if side.startswith("back"):
        command.append("-b")
    command += flatten(map(lambda x: x.split(" ", 1), parameters))
    command.append(libs)
    command.append(boardfilename)
    command.append(svgfilename)
    try:
        output = subprocess.check_output(command, stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as e:
        print("PcbDraw failed with code {} and output: ".format(e.returncode))
        print(e.output)
        sys.exit(1)
    if ext != ".svg":
        if ext == ".png":
            svg_to_png(svgfilename, outputfile)
            os.remove(svgfilename)
        else:
            print("Unsupported image type: {}".format(ext))
            sys.exit(1)

def find_command(list, command):
    for x in list:
        if x.startswith(command):
            return x
    return None

def relativize_header_paths(header, to):
    for key in ["template", "board", "libs"]:
        if key not in header:
            continue
        if os.path.isabs(header[key]):
            continue
        x = os.path.join(to, header[key])
        header[key] = os.path.normpath(x)
    if "params" in header:
        x = header["params"]
        newlist = []
        for key in ["--style", "--remap"]:
            c = find_command(x, key)
            if c is None:
                continue
            y = c.split(" ")
            command, arg = y[0], y[1]
            if os.path.isabs(arg):
                continue
            c = command + " " + os.path.normpath(os.path.join(to, arg))
            newlist.append(c)
        header["params"] = newlist
    return header

def merge_args(args, header):
    for key in filter(lambda x: not x.startswith("_"), dir(args)):
        val = getattr(args, key)
        if val is not None:
            header[key] = val
    if "params" not in header:
        header["params"] = []
    return header

def validate_args(args):
    required = set(["img_name", "type", "output", "input", "board", "libs"])
    missing = required - set(args.keys())
    if missing:
        raise RuntimeError("Missing following parameters: {}"
                           .format(", ".join(missing)))
    if args["type"] == "html":
        if "template" not in args:
            raise RuntimeError("Missing following parameters: template")
    elif args["type"] == "md":
        if "template" in args:
            print("Warning: extra parameter 'template'")
    else:
        raise RuntimeError("Unsupported type parameter, 'md' or 'html' expected, got '{}'"
            .format(args["type"]))
    required.add("template")
    required.add("params")
    extra = set(args.keys()) - required
    for x in extra:
        print("Warning: extra parameter '" + x + "'")
    if args["params"] is None:
        args["params"] = []

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("input", help="source file")
    parser.add_argument("output", help="output directory")
    parser.add_argument("-p", "--params", help="additional flags for PcbDraw")
    parser.add_argument("-b", "--board", help=".kicad_pcb file with a board")
    parser.add_argument("-i", "--img_name", help="image name template, should contain exactly one {{}}")
    parser.add_argument("-t", "--template", help="handlebars template for HTML output")
    parser.add_argument("-f", "--type", help="output type: md or html")
    parser.add_argument("-l", "--libs", help="libraries for PcbDraw")

    args = parser.parse_args()

    try:
        header, content = load_content(args.input)
    except IOError:
        print("Cannot open source file " + args.input)
        sys.exit(1)
    header = relativize_header_paths(header, os.path.dirname(args.input))
    args = merge_args(args, header)

    try:
        validate_args(args)
    except RuntimeError as e:
        print(e.message)
        sys.exit(1)

    if args["type"] == "html":
        renderer = Renderer(mistune.Renderer)
        outputfile = "index.html"
        try:
            template = read_template(args["template"])
        except IOError:
            print("Cannot open template file " + args["template"])
            sys.exit(1)
    else:
        renderer = Renderer(lib.mdrenderer.MdRenderer)
        outputfile = "index.md"
    content = parse_content(renderer, content)
    content = generate_images(content, args["board"], args["libs"],
        args["params"], args["img_name"], args["output"])
    if args["type"] == "html":
        output = generate_html(template, content)
    else:
        output = generate_markdown(content)

    with open(os.path.join(args["output"], outputfile), "wb") as f:
        f.write(output)
