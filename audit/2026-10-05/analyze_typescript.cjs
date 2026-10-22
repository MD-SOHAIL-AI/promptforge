// Read-only source analysis using the project's installed TypeScript parser.
const fs = require('node:fs');
const path = require('node:path');
const ts = require('../../node_modules/typescript');
const root = path.resolve(__dirname, '../..');
const inventory = JSON.parse(fs.readFileSync(path.join(__dirname, 'inventory.json'), 'utf8'));
const records = [];
for (const file of inventory.files) {
  if (!/\.(?:[cm]?[jt]sx?)$/.test(file.path) || file.path.endsWith('.py')) continue;
  const text = fs.readFileSync(path.join(root, file.path), 'utf8');
  const ast = ts.createSourceFile(file.path, text, ts.ScriptTarget.Latest, true);
  const functions = [], imports = [], calls = [];
  function visit(node) {
    if (ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) {
      if (node.moduleSpecifier && ts.isStringLiteral(node.moduleSpecifier)) imports.push(node.moduleSpecifier.text);
    }
    if (ts.isFunctionDeclaration(node) || ts.isMethodDeclaration(node) || ts.isArrowFunction(node) || ts.isFunctionExpression(node)) {
      const start = ast.getLineAndCharacterOfPosition(node.getStart(ast)).line + 1;
      const end = ast.getLineAndCharacterOfPosition(node.end).line + 1;
      let name = node.name?.getText(ast);
      if (!name && ts.isVariableDeclaration(node.parent)) name = node.parent.name.getText(ast);
      functions.push({name: name || '<callback>', line: start, end, size: end-start+1});
    }
    if (ts.isCallExpression(node)) calls.push({name: node.expression.getText(ast).slice(0,160), line: ast.getLineAndCharacterOfPosition(node.getStart(ast)).line+1});
    ts.forEachChild(node, visit);
  }
  visit(ast);
  records.push({path:file.path, functions, imports, calls, parseErrors: ast.parseDiagnostics.map(d=>ts.flattenDiagnosticMessageText(d.messageText,' '))});
}
fs.writeFileSync(path.join(__dirname,'typescript-analysis.json'),JSON.stringify(records,null,2));
console.log(JSON.stringify({files:records.length,parseErrors:records.reduce((n,r)=>n+r.parseErrors.length,0),largest:records.filter(r=>inventory.files.find(f=>f.path===r.path).kind==='owned').flatMap(r=>r.functions.map(f=>({...f,path:r.path}))).sort((a,b)=>b.size-a.size).slice(0,15)},null,2));
