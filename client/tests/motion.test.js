import test from "node:test";
import assert from "node:assert/strict";
import { attachMotion } from "../src/motion.js";

class Events {
  listeners = new Map();
  addEventListener(name, callback) {
    if (!this.listeners.has(name)) this.listeners.set(name, new Set());
    this.listeners.get(name).add(callback);
  }
  removeEventListener(name, callback) {
    this.listeners.get(name)?.delete(callback);
  }
  emit(name, event = {}) {
    for (const callback of this.listeners.get(name) || []) callback(event);
  }
  get listenerCount() {
    return [...this.listeners.values()].reduce(
      (count, set) => count + set.size,
      0,
    );
  }
}

class Element extends Events {
  children = [];
  attributes = new Map();
  properties = new Map();
  style = {
    getPropertyValue: (name) => this.properties.get(name) || "",
    setProperty: (name, value) => this.properties.set(name, value),
    removeProperty: (name) => this.properties.delete(name),
  };
  constructor(top = 0, reveal = false) {
    super();
    this.top = top;
    if (reveal) this.setAttribute("data-reveal", "");
  }
  append(child) {
    this.children.push(child);
    return child;
  }
  remove(child) {
    this.children = this.children.filter((element) => element !== child);
  }
  contains(target) {
    return (
      target === this || this.children.some((child) => child.contains(target))
    );
  }
  matches(selector) {
    return selector === "[data-reveal]" && this.attributes.has("data-reveal");
  }
  querySelectorAll(selector) {
    return this.children.flatMap((child) => [
      ...(child.matches(selector) ? [child] : []),
      ...child.querySelectorAll(selector),
    ]);
  }
  setAttribute(name, value) {
    this.attributes.set(name, value);
  }
  getAttribute(name) {
    return this.attributes.get(name) ?? null;
  }
  removeAttribute(name) {
    this.attributes.delete(name);
  }
  getBoundingClientRect() {
    return { top: this.top };
  }
  get state() {
    return this.getAttribute("data-reveal-state");
  }
}

function fixture({ reduced = false, observer = true } = {}) {
  const root = new Element();
  const view = new Events();
  const media = new Events();
  media.matches = reduced;
  view.matchMedia = (query) => {
    assert.equal(query, "(prefers-reduced-motion: reduce)");
    return media;
  };
  view.innerHeight = 800;
  view.scrollY = 0;
  const frames = new Map();
  let nextFrame = 0;
  view.requestAnimationFrame = (callback) => {
    frames.set(++nextFrame, callback);
    return nextFrame;
  };
  view.cancelAnimationFrame = (id) => frames.delete(id);
  const observers = [];
  if (observer)
    view.IntersectionObserver = class {
      targets = new Set();
      disconnected = false;
      constructor(callback) {
        this.callback = callback;
        observers.push(this);
      }
      observe(element) {
        this.targets.add(element);
      }
      unobserve(element) {
        this.targets.delete(element);
      }
      disconnect() {
        this.disconnected = true;
        this.targets.clear();
      }
      enter(element) {
        this.callback([{ target: element, isIntersecting: true }]);
      }
    };
  const mutations = [];
  view.MutationObserver = class {
    disconnected = false;
    constructor(callback) {
      this.callback = callback;
      mutations.push(this);
    }
    observe(element, options) {
      assert.equal(element, root);
      assert.deepEqual(options, { childList: true, subtree: true });
    }
    disconnect() {
      this.disconnected = true;
    }
  };
  root.ownerDocument = {
    defaultView: view,
    documentElement: { scrollHeight: 2400 },
    activeElement: null,
  };
  const flush = () => {
    const pending = [...frames.values()];
    frames.clear();
    pending.forEach((callback) => callback());
  };
  const mutate = () => mutations[0]?.callback([]);
  return { root, view, media, observers, mutations, frames, flush, mutate };
}

test("only new offscreen sections are armed and they reveal once", () => {
  const { root, observers } = fixture();
  const above = root.append(new Element(-500, true));
  const visible = root.append(new Element(100, true));
  const below = root.append(new Element(1000, true));
  const clean = attachMotion(root);
  assert.equal(above.state, "visible");
  assert.equal(visible.state, "visible");
  assert.equal(below.state, "pending");
  assert.deepEqual([...observers[0].targets], [below]);
  observers[0].enter(below);
  assert.equal(below.state, "revealed");
  assert.equal(observers[0].targets.size, 0);
  clean();
});

test("unsupported observation or preference detection never hides content", () => {
  for (const missing of ["observer", "preference"]) {
    const { root, view } = fixture({ observer: missing !== "observer" });
    if (missing === "preference") delete view.matchMedia;
    const below = root.append(new Element(1200, true));
    const clean = attachMotion(root);
    assert.equal(below.state, "visible");
    clean();
  }
  assert.doesNotThrow(() => attachMotion(null)());
});

test("constructor and individual observation failures leave affected content visible", () => {
  for (const failure of ["constructor", "observe"]) {
    const { root, view } = fixture();
    view.IntersectionObserver = class {
      constructor() {
        if (failure === "constructor") throw new Error("Unavailable");
      }
      observe() {
        throw new Error("Cannot observe");
      }
      unobserve() {}
      disconnect() {}
    };
    const below = root.append(new Element(1200, true));
    const clean = attachMotion(root);
    assert.equal(below.state, "visible");
    clean();
  }
});

test("initial reduced motion disables animation including asynchronous sections", () => {
  const { root, observers, mutate, flush } = fixture({ reduced: true });
  const first = root.append(new Element(1200, true));
  const clean = attachMotion(root);
  const second = root.append(new Element(1300, true));
  mutate();
  flush();
  assert.equal(first.state, "visible");
  assert.equal(second.state, "visible");
  assert.equal(observers.length, 0);
  clean();
});

test("live reduced-motion changes reveal pending content and never re-hide existing sections", () => {
  const { root, media, observers, mutate, flush } = fixture();
  const first = root.append(new Element(1200, true));
  const clean = attachMotion(root);
  media.matches = true;
  media.emit("change");
  assert.equal(first.state, "visible");
  assert.equal(observers[0].disconnected, true);
  observers[0].enter(first);
  assert.equal(
    first.state,
    "visible",
    "a queued old observer callback must honor the preference",
  );
  media.matches = false;
  media.emit("change");
  assert.equal(first.state, "visible");
  const second = root.append(new Element(1500, true));
  mutate();
  flush();
  assert.equal(second.state, "pending");
  assert.equal(observers.length, 2);
  clean();
});

test("keyboard focus reveals all pending ancestors immediately before observer callbacks", () => {
  const { root, observers } = fixture();
  const panel = root.append(new Element(1200, true));
  const card = panel.append(new Element(1300, true));
  const button = card.append(new Element(1350));
  const clean = attachMotion(root);
  root.emit("focusin", { target: button });
  assert.equal(panel.state, "visible");
  assert.equal(card.state, "visible");
  observers[0].enter(panel);
  assert.equal(
    panel.state,
    "visible",
    "a stale intersection must not animate keyboard focus",
  );
  clean();
});

test("a section containing the already focused element is never hidden", () => {
  const { root } = fixture();
  const panel = root.append(new Element(1200, true));
  root.ownerDocument.activeElement = panel.append(new Element(1250));
  const clean = attachMotion(root);
  assert.equal(panel.state, "visible");
  clean();
});

test("async DOM updates are coalesced and removed sections release observer references", () => {
  const { root, observers, mutate, frames, flush } = fixture();
  const first = root.append(new Element(1200, true));
  const clean = attachMotion(root);
  root.remove(first);
  const second = root.append(new Element(1250, true));
  mutate();
  mutate();
  mutate();
  assert.equal(frames.size, 1);
  flush();
  assert.equal(first.state, null);
  assert.equal(second.state, "pending");
  assert.deepEqual([...observers[0].targets], [second]);
  clean();
});

test("scroll progress is throttled, clamped and safe for non-scrollable documents", () => {
  const { root, view, frames, flush } = fixture();
  const clean = attachMotion(root);
  assert.equal(root.style.getPropertyValue("--scroll-progress"), "0");
  view.scrollY = 800;
  view.emit("scroll");
  view.emit("scroll");
  assert.equal(frames.size, 1);
  flush();
  assert.equal(root.style.getPropertyValue("--scroll-progress"), "0.5");
  view.scrollY = 2500;
  view.emit("scroll");
  flush();
  assert.equal(root.style.getPropertyValue("--scroll-progress"), "1");
  view.scrollY = -50;
  view.emit("scroll");
  flush();
  assert.equal(root.style.getPropertyValue("--scroll-progress"), "0");
  root.ownerDocument.documentElement.scrollHeight = 800;
  view.emit("resize");
  flush();
  assert.equal(root.style.getPropertyValue("--scroll-progress"), "0");
  clean();
});

test("cleanup is idempotent and removes pending work, attributes and every listener", () => {
  const { root, view, media, observers, mutations, mutate, frames, flush } =
    fixture();
  root.style.setProperty("--scroll-progress", "0.25");
  const below = root.append(new Element(1200, true));
  const clean = attachMotion(root);
  mutate();
  assert.equal(frames.size, 1);
  clean();
  clean();
  assert.equal(frames.size, 0);
  assert.equal(root.listenerCount, 0);
  assert.equal(view.listenerCount, 0);
  assert.equal(media.listenerCount, 0);
  assert.equal(observers[0].disconnected, true);
  assert.equal(mutations[0].disconnected, true);
  assert.equal(below.state, null);
  assert.equal(root.style.getPropertyValue("--scroll-progress"), "0.25");
  observers[0].enter(below);
  mutate();
  flush();
  assert.equal(below.state, null);
  assert.equal(root.style.getPropertyValue("--scroll-progress"), "0.25");
});
