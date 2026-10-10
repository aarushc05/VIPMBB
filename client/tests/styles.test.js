import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const css = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8");
const rule = (selector) => {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = css.match(new RegExp(`${escaped}\\s*\\{([^}]+)\\}`));
  assert.ok(match, `Missing CSS rule: ${selector}`);
  return match[1];
};

test("scroll reveal states only change opacity, never a control's hit target", () => {
  for (const state of ["pending", "revealed", "visible"]) {
    const declarations = rule(`[data-reveal-state="${state}"]`)
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .split(";")
      .map((value) => value.trim())
      .filter(Boolean);
    for (const declaration of declarations) {
      assert.match(declaration, /^(opacity|transition)\s*:/);
      if (declaration.startsWith("transition")) {
        assert.match(declaration, /^transition:\s*(none|opacity\b[^;]*)$/);
      }
    }
  }
});

test("table scrolling contains absolute accessibility labels", () => {
  const declarations = rule(".table-scroll");
  assert.match(declarations, /position:\s*relative\s*;/);
  assert.match(declarations, /overflow(?:-x)?:\s*auto\s*;/);
});
