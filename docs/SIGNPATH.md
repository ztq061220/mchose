# SignPath Foundation 配置说明

本项目是公开源码项目，计划申请 SignPath Foundation 的免费开源代码签名。

## 1. 申请前检查

- GitHub 仓库公开：`https://github.com/ztq061220/mchose`
- 根目录有 OSI 认可许可证（当前为 MIT）
- GitHub Actions 能构建出未签名产物
- 不提交 `payload.zip`、`release/`、`dist*/` 等生成物
- 角色素材授权见 `assets/README.md`

## 2. 申请

1. 在 <https://signpath.io/> 注册账号。
2. 申请 **SignPath Foundation**（Open Source）计划。
3. 提交仓库地址、许可证、构建说明和发布产物说明。
4. 等待审核。

## 3. 审核通过后创建配置

在 SignPath 控制台创建：

- Organization
- Project：`mchose`
- Signing policy：`release-signing`
- Artifact configuration：
  - `app-exe`：用于 `dist/MCHOSEBatteryWidget/MCHOSEBatteryWidget.exe`
  - `installer-exe`：用于 `release/MCHOSE键鼠电量_安装程序_v1.5.7.exe`

安装 SignPath GitHub App 到本仓库。

## 4. GitHub Secrets

在仓库 Settings → Secrets and variables → Actions 添加：

```text
SIGNPATH_API_TOKEN
SIGNPATH_ORGANIZATION_ID
```

`SIGNPATH_ORGANIZATION_ID` 也可以放到 Variables 中。

## 5. 两阶段签名

因为安装器里嵌入了 app，推荐顺序：

1. 构建 `MCHOSEBatteryWidget.exe`
2. 上传并让 SignPath 签名 app exe
3. 下载已签名 app，重新生成 `payload.zip`
4. 编译安装器
5. 上传安装器 exe，让 SignPath 签名
6. 发布已签名安装器

如果只签安装器，安装目录里的 app exe 仍然是未签名状态。

## 6. GitHub Actions 模板

审核通过、拿到 project / policy / artifact configuration 的 slug 后，在 `.github/workflows/` 增加签名工作流。核心步骤：

```yaml
- uses: actions/upload-artifact@v4
  id: upload
  with:
    name: unsigned
    path: release/MCHOSE键鼠电量_安装程序_v1.5.7.exe

- uses: signpath/github-action-submit-signing-request@v1
  with:
    api-token: ${{ secrets.SIGNPATH_API_TOKEN }}
    organization-id: ${{ secrets.SIGNPATH_ORGANIZATION_ID }}
    project-slug: mchose
    signing-policy-slug: release-signing
    artifact-configuration-slug: installer-exe
    github-artifact-id: ${{ steps.upload.outputs.artifact-id }}
    wait-for-completion: true
    output-artifact-directory: signed
```

具体输入名以 SignPath 控制台生成的 workflow 示例为准。

## 7. 注意

- 签名后不能再修改 exe。
- 必须加时间戳，否则证书过期后签名失效。
- 免费开源签名不保证 SmartScreen 立刻不报警；信誉需要时间积累。
- 不要把证书私钥、API Token 提交到仓库。
