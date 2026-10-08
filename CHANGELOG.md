# 更新日志

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/) 与
[Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/) 规范。

## [1.0.0] - 2026-10-07

### 新增
- 将桂林理工大学教务系统课表转换为符合 **RFC 5545** 的 iCalendar（`.ics`）文件。
- 在线模式：登录教务系统并直连抓取课表（`AcademicClient`）。
- 离线模式：解析浏览器另存的课表 HTML（`--html`），适配 GBK 编码页面。
- 三校区作息档案：雁山（默认）/ 屏风 / 南宁，含桂林"中午1/中午2"节次偏移。
- 重复事件：连续周 / 单双周 / 多段不规则周次的 `RRULE` 拆分。
- 完整时区处理：`DTSTART;TZID=Asia/Shanghai` + 内嵌 `VTIMEZONE`。
- 导入校验：结构配对、必需属性、时间格式、UID 唯一性、RRULE 合法性。
- 命令行入口 `glut-ics` / `python -m glut_ics`。
- 72 项单元测试，含真实课表样本。

[1.0.0]: https://github.com/JennnyK/glut_ics/releases/tag/v1.0.0
