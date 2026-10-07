import { ensureDependencies, python, run } from './runtime.mjs';
ensureDependencies();
run(python, ['-m', 'pytest', 'tests', '-q'], { env: { ...process.env, VIPMBB_DISABLE_MODEL: '1' } });
run('npm', ['--prefix', 'web', 'test']);
run('npm', ['--prefix', 'web', 'run', 'build']);
