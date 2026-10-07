#!/usr/bin/env node
import { startModelService, ensureModels } from './runtime.mjs';
let owned;
try {
  owned = await startModelService();
  await ensureModels();
  console.log('Both local models are installed. Start the complete app with npm run dev.');
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
} finally {
  owned?.kill('SIGTERM');
}
