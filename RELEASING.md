# 分支与发布规则

## 分支约定

- `develop` 是**本地开发分支**，**不允许推送到远程仓库**。
- 远程仓库只保留 `master` 与语义化版本标签。
- 当功能在本地 `develop` 上完成后，通过 `git merge develop` 合并到 `master`，然后按本文件的发布清单一键发布。

## 把 `develop` 合并到 `master` 即发布

将 `master` 分支按最小化原则一键发布至远程仓库，同步更新 `README.md`、`CHANGELOG.md`、`pyproject.toml` 版本号及相关解释性文件，在确认无敏感信息、仅包含项目必需内容且其他用户端可正常使用后，创建并推送语义化标签以触发 GitHub Actions 自动构建并发布到 PyPI。

执行步骤（必须按顺序）：

1. **切到干净状态的 master**
   ```bash
   git checkout master
   git pull origin master
   git status
   ```

2. **更新版本号、CHANGELOG、README 等解释性文件**
   - `pyproject.toml`: bump `version` 到新的 SemVer。
   - `CHANGELOG.md`: 添加 `[x.y.z] - YYYY-MM-DD` 小节总结变更。
   - `README.md`: 如有变更，更新安装说明、功能列表或环境变量表。
   - 其他解释性文档仅在实际受影响时更新。

3. **校验最小化内容**
   - 无 `.env` 文件、凭据、token、密钥或个人数据。
   - 无临时文件、构建产物、`__pycache__`、`.pytest_cache`、`dist/`、`*.egg-info/`。
   - 无无关代码、实验性或进行中的工作。
   - 确认 `.gitignore` 已排除上述内容。

4. **本地跑通质量门**
   ```bash
   pytest tests/ -v
   ruff check src/
   mypy src/
   python -m build
   ```

5. **提交并推送 master**
   ```bash
   git add pyproject.toml CHANGELOG.md README.md
   git commit -m "chore(release): bump version to x.y.z"
   git push origin master
   ```

6. **打语义化标签并推送，触发 release.yml**
   ```bash
   git tag -a vx.y.z -m "Release vx.y.z"
   git push origin vx.y.z
   ```

7. **验证 PyPI 发布包可正常安装**
   ```bash
   pip install umu-skills==x.y.z
   ```

8. **敏感凭据只允许存在于 GitHub Secrets (`PYPI_API_TOKEN`)**，绝不允许写入仓库。
