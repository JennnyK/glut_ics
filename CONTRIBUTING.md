# 贡献指南

感谢你愿意为 `glut-ics` 做出贡献！本文档说明本地开发与提交规范。

## 开发环境

```bash
git clone https://github.com/JennnyK/glut_ics.git
cd glut_ics
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -e .
pip install -r requirements-dev.txt
```

运行时代码**零第三方依赖**，仅需 Python ≥ 3.9。

## 运行测试

```bash
python -m unittest discover -s tests -t . -v
```

提交前请确保：

1. 全部测试通过；
2. 新增功能附带对应单元测试；
3. 不提交任何本人生成的 `.ics`（已在 `.gitignore` 中忽略）。

## 代码风格

- 遵循 PEP 8，模块与公开 API 需有中文 docstring；
- 保持**纯标准库**实现，除非有充分理由，不引入运行时依赖；
- 涉及解析逻辑的改动，请同时更新 `tests/fixtures/` 中的样本或补充用例。

## 提交信息规范

采用 [Conventional Commits](https://www.conventionalcommits.org/zh-hans/)：

```
feat: 支持雁山新作息
fix: 修正 GBK 页面乱码
docs: 补充离线模式说明
test: 覆盖多段周次
```

## 提交 PR

1. Fork 本仓库并从 `main` 创建特性分支（如 `feat/pingfeng-profile`）；
2. 提交前跑通测试；
3. 在 PR 描述中说明动机、改动点与验证方式。

## 报告问题

请通过 [Issues](https://github.com/JennnyK/glut_ics/issues) 反馈，并尽量附上：

- Python 版本与操作系统；
- 运行命令（**请勿粘贴真实账号密码**）；
- 完整报错信息与复现步骤。
