import { createServer } from 'node:http';
import { readFile, stat } from 'node:fs/promises';
import { resolve, extname, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
const root = fileURLToPath(new URL('../docs/', import.meta.url));
const port = Number(process.env.PORT || 4173);
const prefix = `/${(process.env.BASE_PATH || '').replace(/^\/+|\/+$/g,'')}`.replace(/\/$/,'');
const mime = {'.html':'text/html; charset=utf-8','.css':'text/css; charset=utf-8','.js':'text/javascript; charset=utf-8','.json':'application/json; charset=utf-8','.svg':'image/svg+xml','.png':'image/png','.jpg':'image/jpeg','.webp':'image/webp'};
createServer(async (req,res) => {
  try {
    let path = decodeURIComponent(new URL(req.url,'http://localhost').pathname);
    if (prefix && path !== prefix && !path.startsWith(`${prefix}/`)) { res.writeHead(404); res.end('Not found'); return; }
    path = path.slice(prefix.length);
    let file = resolve(root, `.${path || '/'}`);
    if (file !== resolve(root) && !file.startsWith(resolve(root)+sep)) { res.writeHead(403); res.end('Forbidden'); return; }
    if ((await stat(file)).isDirectory()) file = resolve(file,'index.html');
    const body = await readFile(file); res.writeHead(200,{'Content-Type':mime[extname(file)] || 'application/octet-stream','Cache-Control':'no-store'}); res.end(body);
  } catch { res.writeHead(404); res.end('Not found'); }
}).listen(port,'127.0.0.1',() => console.log(`Preview: http://127.0.0.1:${port}${prefix}/`));
