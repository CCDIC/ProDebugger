# OpenClaw offline case / OpenClaw 离线案例

Open `index.html` directly in a browser, including from a `file://` URL. The page embeds its case data, scripts, styles, and IBM Plex fonts. Viewing the saved case requires no server or network access. The interface supports English and Chinese and starts in the dark theme.

直接用浏览器打开 `index.html`，支持 `file://`。案例数据、脚本、样式与 IBM Plex 字体均已内嵌，无需服务或网络即可查看已保存案例。界面支持中英文，默认使用深色主题。

## Build / 构建

With Node.js available, run this command from this directory:

在本目录运行以下命令（需要 Node.js）：

```sh
node build.cjs
```

The build reads `src/`, `assets/case-data.json`, and the shared fonts in `../fonts/`, then writes `index.html` and `FONT-LICENSE.txt`. No npm install is needed. Rebuild after changing the source or sanitized case data. IBM Plex font terms are included in `FONT-LICENSE.txt`.

构建读取 `src/`、`assets/case-data.json` 和共享字体目录 `../fonts/`，生成 `index.html` 和 `FONT-LICENSE.txt`，无需安装 npm 依赖。修改源码或脱敏案例数据后需重新构建。字体许可证见 `FONT-LICENSE.txt`。

## Open the workbench / 打开真实工作台

The top-bar **Open workbench** link opens `http://127.0.0.1:8792/studio.html?risk=1` in a new tab. Start the local ProDebugger service first, following the repository's startup instructions. This link opens the interactive workbench; it does not automatically import or run the saved case. The offline case itself performs no service requests or model calls.

顶栏的 **打开工作台** 会在新标签页打开 `http://127.0.0.1:8792/studio.html?risk=1`。请先按仓库说明启动本机 ProDebugger 服务。链接进入真实交互工作台，不会自动导入或运行该案例。离线页面自身不会请求服务或调用模型。

To use another service address, add a URL-encoded `workbench` query parameter to `index.html`. Only absolute HTTP/HTTPS URLs without embedded credentials are accepted; invalid values fall back to the local default. For example:

若使用其他服务地址，可在 `index.html` 后添加经过 URL 编码的 `workbench` 参数。只接受不含用户名、密码的 HTTP/HTTPS 绝对地址；无效参数会回退至本机默认地址。例如：

```text
index.html?workbench=http%3A%2F%2F127.0.0.1%3A8792%2Fstudio.html%3Frisk%3D1
```
