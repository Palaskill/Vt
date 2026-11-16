/* Build the small React Bits island; deployment serves the checked-in bundle.
 * Install react, react-dom, typescript and webpack locally before rebuilding.
 */
const fs = require('fs');
const os = require('os');
const path = require('path');
const ts = require('typescript');
let webpack;
let terser;
try {
  webpack = require('webpack');
} catch (_) {
  webpack = require('next/dist/compiled/webpack/webpack').webpack;
}
try {
  terser = require('terser');
} catch (_) {
  terser = require('next/dist/compiled/terser');
}

const root = path.resolve(__dirname, '..');
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'vetstock-spotlight-'));
for (const name of ['SpotlightCard', 'spotlight-entry']) {
  let source = fs.readFileSync(path.join(__dirname, name + '.jsx'), 'utf8');
  // The upstream stylesheet is served alongside the application stylesheet.
  source = source.replace("import './SpotlightCard.css';", '');
  const result = ts.transpileModule(source, {
    compilerOptions: {
      jsx: ts.JsxEmit.ReactJSX,
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020
    }
  });
  fs.writeFileSync(path.join(temporary, name + '.js'), result.outputText);
}

const moduleDirs = [
  path.join(__dirname, 'node_modules'),
  path.join(root, 'node_modules'),
  ...(process.env.NODE_PATH || '').split(path.delimiter).filter(Boolean),
  'node_modules'
];
webpack({
  mode: 'production',
  // Keep the build independent of a separate minifier installation.
  optimization: { minimize: false },
  entry: path.join(temporary, 'spotlight-entry.js'),
  devtool: false,
  output: { path: path.join(root, 'static'), filename: 'react-spotlight.js' },
  resolve: { modules: moduleDirs }
}, async (error, stats) => {
  fs.rmSync(temporary, { recursive: true, force: true });
  if (error || stats.hasErrors()) {
    console.error(error || stats.toString({ colors: false, errors: true }));
    process.exitCode = 1;
    return;
  }
  try {
    const bundle = path.join(root, 'static', 'react-spotlight.js');
    const minified = await terser.minify(fs.readFileSync(bundle, 'utf8'), {
      ecma: 2020,
      compress: true,
      mangle: true,
      format: { comments: false }
    });
    if (!minified.code) throw new Error('The spotlight bundle is empty.');
    fs.writeFileSync(bundle, minified.code + '\n');
    console.log('Built React Bits SpotlightCard: ' + fs.statSync(bundle).size + ' bytes');
  } catch (buildError) {
    console.error(buildError);
    process.exitCode = 1;
  }
});
