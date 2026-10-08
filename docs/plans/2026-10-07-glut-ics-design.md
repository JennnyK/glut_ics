# 桂林理工大学课表 → iCalendar 转换器 设计文档

日期：2026-10-07

## 目标

将桂林理工大学教务系统的课表数据转换为符合 RFC 5545 的 `.ics` 文件，
可直接导入 Google Calendar / Apple Calendar / Outlook。要求：

1. 采用参考仓库 [hzhkdh/glut-schedule](https://github.com/hzhkdh/glut-schedule) 的数据获取方式（登录直连抓取）；
2. 提取课程名称、上课时间、地点、教师等字段；
3. 生成 RFC 5545 标准 ICS；
4. 支持重复课程事件、时区处理、日历导入验证。

## 技术选型

* **Python 3.9+，纯标准库**：`urllib` + `http.cookiejar` 处理登录与会话，
  `re` 解析 HTML，手写 ICS 渲染。零依赖便于跨平台直接运行。
* 交付形态：可导入的包 `glut_ics` + `python -m glut_ics` 命令行。

## 数据获取（对应仓库 `service/academic/`）

```
GET  {base}/academic/affairLogin.do                    # 取 JSESSIONID
GET  {base}/academic/j_acegi_security_check;jsessionid=…?j_username=&j_password=&j_captcha=
POST {base}/academic/personal/framePage.do             # 校验登录 + 读学期信息
GET  {base}/academic/student/currcourse/currcourse.jsdo?year=&term=   # 取内部学号
GET  {base}/academic/manager/coursearrange/showTimetable.do?id=&yearid=&termid=&timetableType=STUDENT&sectionType=BASE
GET  {base}/academic/manager/coursearrange/studentWeeklyTimetable.do?yearid=&termid=  # 仅取总周数
```

* 桂林 `https://jw.glut.edu.cn`，南宁 `http://jw.glutnn.cn`（白名单）。
* `yearid = 年份 - 1980`；`termid`：春=1，秋(桂林)=2，秋(南宁)=3。
* 验证码/统一认证场景提供 OA 兜底（`ca.glut.edu.cn:8888/zfca`）。

## 解析（对应仓库 `service/parser/AcademicScheduleParser.kt`）

大节课表单元格以"行"组织：`<<课程名>>;序号` → 教室 → 教师 → 周次 → 学时类型。

关键规则（逐条移植）：

* `htmlToLines`：**先去标签、后解码实体**，否则 `&lt;&lt;课程名&gt;&gt;` 会被当标签删除。
* 课程按 `<<…>>` 划块；块内首行判定教室（`looksLikeRoom`，含 NFKC 规范化），
  余下非"学时类型"行为教师/周次。
* 周次查找顺序：显式（含"周"）→ 分片单双周 → 紧凑；避免把实验课的课序 `2-1` 当周次。
* 节次：`parseDisplaySectionRange` + `offsetSectionForNoon`，桂林第 5 节起 +2。
* 调课表：按 `类型/课程号/课程名/课序号/教师 …` 解析，`调课` 移除原周次，
  `补课` 追加补课时段并去重，`停课`/`代课` 保留原时段。
* 合并：同 `id` 合并课次、相邻节次合并为区间、按 `标题|教师|教室` 归并。

## ICS 生成（RFC 5545）

* `VCALENDAR` 含 `VERSION:2.0`、`PRODID`、`CALSCALE`、`METHOD:PUBLISH`、
  `X-WR-CALNAME`、`X-WR-TIMEZONE`，并内嵌 `VTIMEZONE`（`Asia/Shanghai`，固定 +0800）。
* 每个课次（星期 + 节次区间 + 教室）对应一个 `VEVENT`：
  `UID`、`DTSTAMP`、`DTSTART;TZID=…`、`DTEND;TZID=…`、`RRULE`、`SUMMARY`、
  `LOCATION`、`DESCRIPTION`（教师/周次/节次/学时类型/地点）。
* **重复事件**：RFC 5545 规定一个 `VEVENT` 只能有一个 `RRULE`，因此把周次集合
  拆成等差数列 `(起始周, 步长, 个数)`：步长 1 → `COUNT=n`；步长 2 → `INTERVAL=2;COUNT=n`；
  多段 → 多条 `VEVENT`。
* **行折叠**：>75 八位组按 §3.1 折叠，续行以空格开头，按字符累积字节避免劈开多字节。
* **转义**：`\ ; , 换行` 按 §3.3.11 处理。

## 误差与边界

* 周次无法识别 → 该课次跳过并在报告里列出（不兜底成全周）。
* 节次超出作息表 → 跳过并报警。
* `maxWeek` 解析优先级：学期起止日期 → 周次落地页 → 课表反推 → 20。

## 验证

* `validate_ics`：组件配对、`VERSION/PRODID`、`VEVENT` 必需属性、
  `DTSTART` 格式与时区、`UID` 唯一性、`RRULE` 合法性。
* 单元测试 46 项，含参考仓库真实南宁课表样本与合成桂林样本（含中午节次）。

## 真实账号联调中发现并修复的问题

首版交付后，用真实账号（桂林）联调时发现两处导致"课表为空"的问题：

1. **响应编码**：教务课表页是 **GBK** 编码（`<Meta ... Charset=gbk>`），原实现固定按
   UTF-8 解码，导致"中午/节/周/星期"等中文全部乱码，解析器认不出任何结构 → 0 门课。
   修复：`_decode_body` 按 `HTTP 头 charset → 页面 <meta> → UTF-8 → GB18030 → 兜底` 逐级解码。

2. **作息时间不能从页面推断**：学校教务处说明——*"由于两校区上课时间不一样，教务管理
   信息系统上的课表只能显示一个校区（屏风校区）上课时间"*。即页面时间**恒为屏风**，
   不携带校区信息；雁山学生照抄必然错（第 1 节 08:20 vs 08:30）。
   中间曾实现"默认采信页面时间"，反而**静默覆盖**了用户显式给出的 `--profile`，
   无法纠正。最终方案：**作息完全由 `--profile` 决定（桂林默认雁山 08:30）**，
   页面时间不参与计算，仅在同一校区的雁山用户上打印一条提示，避免误用。
   （`extract_section_times` 因此退化为纯提示用途。）

修复后实测：18 门课 / 28 个课次 / 30 个日历事件，且能被第三方 `icalendar` 库正确解析
（`DTSTART` 带 `TZID=Asia/Shanghai`，`RRULE` 正常）。

## 未覆盖 / 后续可扩展

* 考试安排、成绩等其它模块（本工具只做课表）。
* 验证码识别（当前遇到即提示改用离线模式）。
* 提醒（`VALARM`）、课程配色（`COLOR`）等增强项。
