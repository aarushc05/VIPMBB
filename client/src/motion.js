const REVEAL_SELECTOR = "[data-reveal]";
const STATE_ATTRIBUTE = "data-reveal-state";

/** Progressive enhancement: markup is visible until an offscreen reveal is armed. */
export function attachMotion(
  root,
  { view = root?.ownerDocument?.defaultView } = {},
) {
  if (!root || !view) return () => {};

  const tracked = new Set();
  const media = view.matchMedia?.("(prefers-reduced-motion: reduce)");
  const previousProgress = root.style.getPropertyValue("--scroll-progress");
  let observer = null;
  let mutations = null;
  let frame = null;
  let disposed = false;
  let scanRequired = false;

  const show = (element, immediate = false) => {
    element.setAttribute(STATE_ATTRIBUTE, immediate ? "visible" : "revealed");
    observer?.unobserve(element);
  };

  const ensureObserver = () => {
    // Without a preference query or observer, leave all content unanimated.
    if (observer || !media || media.matches || !view.IntersectionObserver)
      return;
    try {
      observer = new view.IntersectionObserver(
        (entries) => {
          if (disposed || media.matches) return;
          for (const entry of entries) {
            if (
              entry.isIntersecting &&
              tracked.has(entry.target) &&
              entry.target.getAttribute(STATE_ATTRIBUTE) === "pending"
            )
              show(entry.target);
          }
        },
        { threshold: 0, rootMargin: "0px 0px -24px 0px" },
      );
    } catch {
      observer = null;
    }
  };

  const scan = () => {
    for (const element of tracked) {
      if (!root.contains(element)) {
        observer?.unobserve(element);
        element.removeAttribute(STATE_ATTRIBUTE);
        tracked.delete(element);
      }
    }
    const elements = [...root.querySelectorAll(REVEAL_SELECTOR)];
    if (root.matches?.(REVEAL_SELECTOR)) elements.unshift(root);
    for (const element of elements) {
      if (tracked.has(element)) continue;
      tracked.add(element);
      const focused = element.contains(root.ownerDocument?.activeElement);
      const belowViewport =
        element.getBoundingClientRect().top >= view.innerHeight;
      if (observer && belowViewport && !focused) {
        // Observe first: a broken observer must never leave content hidden.
        try {
          observer.observe(element);
          element.setAttribute(STATE_ATTRIBUTE, "pending");
        } catch {
          show(element, true);
        }
      } else {
        show(element, true);
      }
    }
  };

  const updateProgress = () => {
    const documentElement = root.ownerDocument?.documentElement;
    const distance = Math.max(
      0,
      (documentElement?.scrollHeight || 0) - view.innerHeight,
    );
    const progress =
      distance > 0
        ? Math.min(1, Math.max(0, (view.scrollY || 0) / distance))
        : 0;
    root.style.setProperty("--scroll-progress", String(progress));
  };

  const flush = () => {
    frame = null;
    if (disposed) return;
    if (scanRequired) {
      scanRequired = false;
      scan();
    }
    updateProgress();
  };

  const schedule = () => {
    if (disposed || frame !== null) return;
    if (view.requestAnimationFrame) frame = view.requestAnimationFrame(flush);
    else flush();
  };

  const onFocus = (event) => {
    // Revealing every containing section also handles nested animated cards.
    for (const element of tracked) {
      if (element.contains(event.target)) show(element, true);
    }
  };

  const onPreference = () => {
    if (media.matches) {
      observer?.disconnect();
      observer = null;
      for (const element of tracked) show(element, true);
    } else {
      // Already exposed content stays exposed; only new sections may animate.
      ensureObserver();
    }
  };

  ensureObserver();
  scan();
  updateProgress();
  root.addEventListener("focusin", onFocus);
  view.addEventListener("scroll", schedule, { passive: true });
  view.addEventListener("resize", schedule, { passive: true });
  if (media?.addEventListener) media.addEventListener("change", onPreference);
  else media?.addListener?.(onPreference);

  if (view.MutationObserver) {
    mutations = new view.MutationObserver(() => {
      scanRequired = true;
      schedule();
    });
    // Child changes cover asynchronous React content, without observing our own attributes.
    mutations.observe(root, { childList: true, subtree: true });
  }

  return () => {
    if (disposed) return;
    disposed = true;
    observer?.disconnect();
    mutations?.disconnect();
    if (frame !== null) view.cancelAnimationFrame?.(frame);
    root.removeEventListener("focusin", onFocus);
    view.removeEventListener("scroll", schedule);
    view.removeEventListener("resize", schedule);
    if (media?.removeEventListener)
      media.removeEventListener("change", onPreference);
    else media?.removeListener?.(onPreference);
    for (const element of tracked) element.removeAttribute(STATE_ATTRIBUTE);
    tracked.clear();
    if (previousProgress)
      root.style.setProperty("--scroll-progress", previousProgress);
    else root.style.removeProperty("--scroll-progress");
  };
}
