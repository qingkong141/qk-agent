import resolve from '@rollup/plugin-node-resolve'
import typescript from '@rollup/plugin-typescript'

export default {
  input: 'src/index.ts',
  output: [
    { file: 'dist/index.mjs', format: 'esm', sourcemap: true },
    { file: 'dist/index.cjs', format: 'cjs', sourcemap: true, exports: 'named' },
    { file: 'dist/index.umd.js', format: 'umd', name: 'AgentSDK', sourcemap: true },
  ],
  plugins: [
    resolve(),
    typescript({ tsconfig: './tsconfig.json' }),
  ],
}
