'use strict';

const path = require('path');
const { spawnSync } = require('child_process');

// PyInstaller produces a native executable. Do not let electron-builder
// silently create an arm64 DMG containing an x64 server (or vice versa).
const archFlag = {
  x64: '--x64',
  arm64: '--arm64',
}[process.arch];

if (!archFlag) {
  console.error(`Unsupported macOS host architecture: ${process.arch}`);
  process.exit(1);
}

function run(command, args) {
  const result = spawnSync(command, args, {
    cwd: __dirname,
    stdio: 'inherit',
    shell: process.platform === 'win32',
  });
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status || 1);
}

console.log(`Building a native macOS ${process.arch} server and DMG…`);
run(process.platform === 'win32' ? 'npm.cmd' : 'npm', ['run', 'build:server']);

const builder = path.join(
  __dirname,
  'node_modules',
  '.bin',
  process.platform === 'win32' ? 'electron-builder.cmd' : 'electron-builder',
);
run(builder, ['--mac', archFlag]);