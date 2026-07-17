#!/usr/bin/env node
/** Deterministically render a validated Ambient deck specification. */

import { createRequire } from "node:module";
import { promises as fs } from "node:fs";
import path from "node:path";
import process from "node:process";
import { pathToFileURL } from "node:url";

function parseArgs(argv) {
  const values = {};
  for (let index = 0; index < argv.length; index += 2) {
    const name = argv[index];
    const value = argv[index + 1];
    if (!name?.startsWith("--") || !value) {
      throw new Error("expected --spec, --root, --workspace, and --preview-dir values");
    }
    values[name.slice(2)] = value;
  }
  for (const required of ["spec", "root", "workspace", "preview-dir"]) {
    if (!values[required]) throw new Error(`missing --${required}`);
  }
  return Object.freeze(values);
}

async function loadArtifactTool(workspace) {
  const require = createRequire(path.join(path.resolve(workspace), "package.json"));
  const entrypoint = require.resolve("@oai/artifact-tool");
  return import(pathToFileURL(entrypoint).href);
}

async function writeBlob(outputPath, blob) {
  await fs.mkdir(path.dirname(outputPath), { recursive: true });
  await fs.writeFile(outputPath, new Uint8Array(await blob.arrayBuffer()));
}

function contentType(assetPath) {
  const extension = path.extname(assetPath).toLowerCase();
  const types = new Map([
    [".jpeg", "image/jpeg"],
    [".jpg", "image/jpeg"],
    [".png", "image/png"],
    [".svg", "image/svg+xml"],
    [".webp", "image/webp"],
  ]);
  const result = types.get(extension);
  if (!result) throw new Error(`unsupported deck asset type: ${extension}`);
  return result;
}

function position(element) {
  return {
    left: element.x,
    top: element.y,
    width: element.width,
    height: element.height,
  };
}

async function addAsset(slide, element, root) {
  const assetPath = path.resolve(root, element.asset);
  const bytes = await fs.readFile(assetPath);
  slide.images.add({
    blob: bytes,
    contentType: contentType(assetPath),
    alt: element.alt ?? path.basename(assetPath),
    fit: element.fit ?? "contain",
    position: position(element),
  });
}

function addText(slide, element, index) {
  const shape = slide.shapes.add({
    geometry: "textbox",
    name: element.name ?? `text-${index + 1}`,
    position: position(element),
    fill: "none",
    line: { style: "solid", fill: "none", width: 0 },
  });
  shape.text = element.text;
  shape.text.style = {
    autoFit: element.auto_fit ?? "shrinkText",
    bold: element.bold ?? false,
    color: element.color ?? "#000000",
    fontSize: element.font_size ?? 18,
    insets: element.insets ?? { top: 0, right: 0, bottom: 0, left: 0 },
    typeface: element.typeface,
    verticalAlignment: element.vertical_alignment ?? "top",
    alignment: element.alignment ?? "left",
  };
}

function addShape(slide, element, index) {
  slide.shapes.add({
    geometry: element.geometry ?? "rect",
    name: element.name ?? `shape-${index + 1}`,
    position: position(element),
    fill: element.fill ?? "none",
    line: element.line ?? { style: "solid", fill: "none", width: 0 },
  });
}

function addLine(slide, element, index) {
  slide.shapes.add({
    geometry: "line",
    name: element.name ?? `line-${index + 1}`,
    position: position(element),
    fill: "none",
    line: element.line ?? { style: "solid", fill: "#000000", width: 1 },
  });
}

async function addElement(slide, element, index, root) {
  if (element.type === "asset") return addAsset(slide, element, root);
  if (element.type === "text") return addText(slide, element, index);
  if (element.type === "shape") return addShape(slide, element, index);
  if (element.type === "line") return addLine(slide, element, index);
  throw new Error(`unsupported element type: ${element.type}`);
}

async function buildPresentation(document, root, tool) {
  const presentation = tool.Presentation.create({
    slideSize: document.content.slide_size,
  });
  for (const slideSpec of document.content.slides) {
    const slide = presentation.slides.add();
    slide.background.fill = slideSpec.background ?? "#FFFFFF";
    for (const [index, element] of slideSpec.elements.entries()) {
      await addElement(slide, element, index, root);
    }
    slide.speakerNotes.textFrame.setText(slideSpec.speaker_notes);
    slide.speakerNotes.setVisible(true);
  }
  return presentation;
}

async function exportPreviews(presentation, previewDir) {
  await fs.mkdir(previewDir, { recursive: true });
  const layoutDir = path.join(previewDir, "layout");
  await fs.mkdir(layoutDir, { recursive: true });
  for (const [index, slide] of presentation.slides.items.entries()) {
    const stem = `slide-${String(index + 1).padStart(2, "0")}`;
    await writeBlob(
      path.join(previewDir, `${stem}.png`),
      await presentation.export({ slide, format: "png", scale: 1 }),
    );
    const layout = await slide.export({ format: "layout" });
    await fs.writeFile(path.join(layoutDir, `${stem}.json`), await layout.text());
  }
  await writeBlob(
    path.join(previewDir, "montage.webp"),
    await presentation.export({ format: "webp", montage: true, scale: 1 }),
  );
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const root = path.resolve(args.root);
  const document = JSON.parse(await fs.readFile(path.resolve(args.spec), "utf8"));
  if (document.artifact?.kind !== "deck") throw new Error("spec is not a deck");
  const outputs = document.artifact.outputs.filter((value) => value.endsWith(".pptx"));
  if (outputs.length !== 1) throw new Error("deck must declare exactly one PPTX output");
  const tool = await loadArtifactTool(args.workspace);
  const presentation = await buildPresentation(document, root, tool);
  await exportPreviews(presentation, path.resolve(args["preview-dir"]));
  const output = path.resolve(root, outputs[0]);
  await fs.mkdir(path.dirname(output), { recursive: true });
  const pptx = await tool.PresentationFile.exportPptx(presentation);
  await pptx.save(output);
  console.log(output);
}

main().catch((error) => {
  console.error(`Ambient deck render failed: ${error.message}`);
  process.exitCode = 1;
});
