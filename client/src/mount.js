// Symbol.for survives entry-module re-evaluation during Vite hot updates.
const ROOT_KEY = Symbol.for('vipmbb.react.root');

export function renderApp(container, content, createRoot) {
  if (!container) throw new Error('The application root element is missing.');
  const root = container[ROOT_KEY] || (container[ROOT_KEY] = createRoot(container));
  root.render(content);
  return root;
}
