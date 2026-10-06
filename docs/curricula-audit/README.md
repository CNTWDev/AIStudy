# 教材校对报告（2026-10-06）

这份报告对照权威来源，校对了六个教材包，没有拿大模型自己的记忆当依据。用到的来源有：上海市教委每学期发布的《教学用书目录》、教育部国家课程教学用书目录、上海市教育考试院的考试办法和点评，以及剑桥国际官网的大纲 PDF。

校对时守了几条规矩：
- 每条改动、每个新增知识点都附上来源链接。
- 查不到权威依据的地方不改，记进 `*.unverified.json`。
- 知识点 id 一个不改，旧的学习记录和诊断结果照常有效。

## 结果

| 教材包 | 改正 | 新增知识点 | 最重要的发现 | 详细报告 |
|---|---|---|---|---|
| 统编语文 chn-tongbian | 16 | 20 | 上海用的是**五·四学制版**，不是六三制。高中有单元写错；高三上学期用的选择性必修下册原来整册漏了 | [chn-tongbian.md](chn-tongbian.md) |
| 沪教数学 math-shanghai | 53 | 10 | 1–9 年级 2024–2026 年**全部换成新教材**（李大潜主编）；高中多处册次、章号写错（数列、计数原理、概率统计） | [math-shanghai.md](math-shanghai.md) |
| 上海英语 eng-shanghai | 17 | 3 | **2026 秋起，牛津上海版、新世纪版已从目录里消失**，3–9 年级改用新版（束定芳主编），1–2 年级用上海编写的《英语》；高考英语 2025 年起听力并入听说机考（笔试 115 分 + 听说 35 分） | [eng-shanghai.md](eng-shanghai.md) |
| 上海物理 phy-shanghai | 118 | 16 | 初中、高中都是**上海科学技术出版社**，初中已换新版；七年级不再开科学课；高中章名原来混用了人教版 | [phy-shanghai.md](phy-shanghai.md) |
| 剑桥英语 eng-cambridge | 43 | 38 | IGCSE ESL 2024 年改版：取消 summary，听力全部改成选择题；补齐 0500 第一语言英语；11 个语法点按官方框架往前调 | [eng-cambridge.md](eng-cambridge.md) |
| 剑桥物理 phy-cambridge | 41 | 16 | IGCSE 0625 的 45 个小节都已覆盖，修正了 Core/Supplement 的划分；Lower Secondary 0893 从 5 个点补到 18 个 | [phy-cambridge.md](phy-cambridge.md) |

合计改正 288 处，新增 103 个知识点，删除 0 个。所有教材包的 `version` 升到 2，并加上 `verified: 2026-10-06`。

## 还要补的

**新版课本的单元目录。** 新版五·四学制课本的单元目录，大多只有图片版，或者网站不让程序访问，所以下面这些册的单元号先保持原样：
- 英语 1–9 年级各册
- 数学二至五年级、六下至九下
- 物理八下、九下
- 语文六上，以及新版四、五、八、九年级

最可靠的补法是拍下孩子手上课本的目录页。

**还没做的教材包。** 下面按优先级列出，详见 `landscape.json` 的 gaps：
- P0：剑桥数学（Lower Secondary 0862 → IGCSE 0580/0606）
- P1：IGCSE 化学 0620、生物 0610
- P2：上海小学科学（1–6 年级，沪科技新版）、上海化学（八年级起开设）
- P3：IGCSE 中文、道德与法治、历史、IB、AP

## 上海教材全景

### 上海公办（2026 秋）

| 科目 | 年级 | 教材 | 出版社 | 起用 | 来源 |
|---|---|---|---|---|---|
| 学制 | 1-12 | 五·四学制（小学1-5、初中6-9、高中10-12），2026秋目录仍标五·四学制，未查到调整 | - | 沿用 | [链接](https://edu.sh.gov.cn/cmsres/7a/7aed3909441a4c849d5d78b8d914f4fb/00e155989221056fb74efd6f967939f0.pdf) |
| 语文 | G1-G9 | 统编 义务教育教科书（五·四学制）·语文（温儒敏、王立军）+上海练习部分 | 人民教育出版社 | 已在用（2026春目录已列） | [链接](https://jw.beijing.gov.cn/xxgk/2024zcwj/2024qtwj/202408/W020240905543232813376.pdf ; https://edu.sh.gov.cn/cmsres/7a/7aed3909441a4c849d5d78b8d914f4fb/00e155989221056fb74efd6f967939f0.pdf) |
| 数学 | G1-G5 | 义务教育教科书（五·四学制）·数学（李大潜），国审沪教版；取代数学（试用本） | 上海教育出版社 | G1 2024秋；G2 2025秋；G3-G5 2026秋 | [链接](https://edu.sh.gov.cn/cmsres/97/9705ef64871d45cf984f73d79cae5fd9/d5352cc408d180944636941826a5cdd3.pdf ; https://edu.sh.gov.cn/cmsres/2d/2dc160ca4658483e8eaaf76e8f945637/28a7fabd94b936378d6dd4c271c12ad8.pdf ; https://edu.sh.gov.cn/cmsres/7a/7aed3909441a4c849d5d78b8d914f4fb/00e155989221056fb74efd6f967939f0.pdf) |
| 数学 | G6-G9 | 义务教育教科书（五·四学制）·数学（李大潜） | 上海教育出版社 | G6-G7 2024秋；G8 2025秋；G9 2026秋 | [链接](https://edu.sh.gov.cn/cmsres/97/9705ef64871d45cf984f73d79cae5fd9/d5352cc408d180944636941826a5cdd3.pdf ; https://edu.sh.gov.cn/cmsres/7a/7aed3909441a4c849d5d78b8d914f4fb/00e155989221056fb74efd6f967939f0.pdf) |
| 英语 | G1-G2 | 英语（上海编写，一/二年级），取代牛津上海版1A-2B | 上海教育出版社 | G1 2024秋；G2 2025秋 | [链接](https://edu.sh.gov.cn/cmsres/97/9705ef64871d45cf984f73d79cae5fd9/d5352cc408d180944636941826a5cdd3.pdf ; https://edu.sh.gov.cn/cmsres/7a/7aed3909441a4c849d5d78b8d914f4fb/00e155989221056fb74efd6f967939f0.pdf) |
| 英语 | G3-G5 | 义务教育教科书（五·四学制）·英语（束定芳），取代牛津上海版/新世纪版试用本 | 上海教育出版社 | 2026秋 | [链接](https://edu.sh.gov.cn/cmsres/3a/3a555417ed2b47a38f730ca8c9f10881/9ba8430bf7e54d3e19fce9c17b45f5fb.pdf ; https://edu.sh.gov.cn/cmsres/7a/7aed3909441a4c849d5d78b8d914f4fb/00e155989221056fb74efd6f967939f0.pdf) |
| 英语 | G6-G9 | 义务教育教科书（五·四学制）·英语（束定芳） | 上海教育出版社 | G6-G7 2024秋；G8 2025秋；G9 2026秋 | [链接](https://edu.sh.gov.cn/cmsres/7a/7aed3909441a4c849d5d78b8d914f4fb/00e155989221056fb74efd6f967939f0.pdf ; https://screle.shisu.edu.cn/ea/de/c16360a191198/page.htm) |
| 科学 | G1-G6 | 义务教育教科书（五·四学制）·科学（俞立中）+活动手册；取代自然（试用本） | 上海科学技术出版社 | G1、G6 2024秋；G2 2025秋；G3-G5 2026秋 | [链接](https://jw.beijing.gov.cn/xxgk/2024zcwj/2024qtwj/202408/W020240905543232813376.pdf ; https://edu.sh.gov.cn/cmsres/7a/7aed3909441a4c849d5d78b8d914f4fb/00e155989221056fb74efd6f967939f0.pdf) |
| 道德与法治 | G1-G9 | 统编（五·四学制）+活动册/练习部分 | 人民教育出版社 | 已在用 | [链接](https://edu.sh.gov.cn/cmsres/7a/7aed3909441a4c849d5d78b8d914f4fb/00e155989221056fb74efd6f967939f0.pdf) |
| 信息科技 | G3-G8 | 未查到课本（教育部2024目录与上海2026秋目录均无） | 未查到 | - | [链接](https://jw.beijing.gov.cn/xxgk/2024zcwj/2024qtwj/202408/W020240905543232813376.pdf) |
| 艺术·美术 | G1-G9 | 义务教育教科书（五·四学制）·艺术·美术（潘耀昌）+实践手册 | 上海书画出版社 | G1、G6-G7 2024秋；G2、G8 2025秋；G3-G5、G9 2026秋 | [链接](https://edu.sh.gov.cn/cmsres/7a/7aed3909441a4c849d5d78b8d914f4fb/00e155989221056fb74efd6f967939f0.pdf) |
| 艺术·音乐 | G1-G9 | 教育部目录有五·四学制艺术·音乐（余丹红）；2026春G3-G5仍为音乐（试用本）；2026秋情况未确认 | 上海音乐出版社 | 未确认 | [链接](https://jw.beijing.gov.cn/xxgk/2024zcwj/2024qtwj/202408/W020240905543232813376.pdf ; https://edu.sh.gov.cn/cmsres/3a/3a555417ed2b47a38f730ca8c9f10881/9ba8430bf7e54d3e19fce9c17b45f5fb.pdf) |
| 物理 | G8-G9 | 义务教育教科书（五·四学制）·物理（高景）+物理综合活动手册（推断）；取代物理（试用本，沪教育） | 上海科学技术出版社 | G8 2024秋；G9 2025秋 | [链接](https://jw.beijing.gov.cn/xxgk/2024zcwj/2024qtwj/202408/W020240905543232813376.pdf ; https://edu.sh.gov.cn/cmsres/97/9705ef64871d45cf984f73d79cae5fd9/d5352cc408d180944636941826a5cdd3.pdf ; https://edu.sh.gov.cn/cmsres/2d/2dc160ca4658483e8eaaf76e8f945637/28a7fabd94b936378d6dd4c271c12ad8.pdf) |
| 化学 | G8-G9 | 义务教育教科书（五·四学制）·化学（麻生明、陈寅）+化学综合活动手册（推断）；八年级新开设 | 上海科学技术出版社 | G8 2024秋；G9 2025秋 | [链接](https://m.thepaper.cn/detail/28421343 ; https://edu.sh.gov.cn/cmsres/97/9705ef64871d45cf984f73d79cae5fd9/d5352cc408d180944636941826a5cdd3.pdf) |
| 生物学 | G7-G8 | 义务教育教科书（五·四学制）·生物学（胡兴昌）+综合活动手册（推断）；取代生命科学（试用本） | 上海教育出版社 | G7 2024秋；G8 2025秋 | [链接](https://jw.beijing.gov.cn/xxgk/2024zcwj/2024qtwj/202408/W020240905543232813376.pdf ; https://edu.sh.gov.cn/cmsres/2d/2dc160ca4658483e8eaaf76e8f945637/28a7fabd94b936378d6dd4c271c12ad8.pdf) |
| 地理 | G6-G7 | 义务教育教科书（五·四学制）·地理（段玉山）+练习部分 | 中华地图学社、中国地图出版社 | 2024秋 | [链接](https://jw.beijing.gov.cn/xxgk/2024zcwj/2024qtwj/202408/W020240905543232813376.pdf ; https://edu.sh.gov.cn/cmsres/97/9705ef64871d45cf984f73d79cae5fd9/d5352cc408d180944636941826a5cdd3.pdf) |
| 历史 | G7-G9 | 统编（五·四学制）中国历史1-4册、世界历史1-2册（张海鹏、徐蓝）+练习部分+地图册（中图社） | 人民教育出版社 | 已在用 | [链接](https://edu.sh.gov.cn/cmsres/7a/7aed3909441a4c849d5d78b8d914f4fb/00e155989221056fb74efd6f967939f0.pdf) |
| 语文/思想政治/历史 | G10-G12 | 普通高中教科书（统编） | 人民教育出版社 | 2020秋 | [链接](https://edu.sh.gov.cn/cmsres/ef/ef2f1a35df7b4f9e9e4e49238b5e025d/7e9bd8c474ea0b7e2008b48bc1b96374.pdf) |
| 数学 | G10-G12 | 普通高中教科书·数学 必修1-4、选择性必修1-3（沪教版） | 上海教育出版社 | 2020秋 | [链接](https://edu.sh.gov.cn/cmsres/ef/ef2f1a35df7b4f9e9e4e49238b5e025d/7e9bd8c474ea0b7e2008b48bc1b96374.pdf ; https://edu.sh.gov.cn/cmsres/7a/7aed3909441a4c849d5d78b8d914f4fb/00e155989221056fb74efd6f967939f0.pdf) |
| 英语 | G10-G12 | 普通高中教科书·英语 必修1-3、选择性必修1-4；沪教版或上外版由学校选一种 | 上海教育出版社 / 上海外语教育出版社 | 2020秋 | [链接](https://edu.sh.gov.cn/cmsres/7a/7aed3909441a4c849d5d78b8d914f4fb/00e155989221056fb74efd6f967939f0.pdf) |
| 物理 | G10-G12 | 普通高中教科书·物理 必修1-3、选择性必修1-3 | 上海科学技术出版社 | 2020秋 | [链接](https://edu.sh.gov.cn/cmsres/ef/ef2f1a35df7b4f9e9e4e49238b5e025d/7e9bd8c474ea0b7e2008b48bc1b96374.pdf ; https://edu.sh.gov.cn/cmsres/7a/7aed3909441a4c849d5d78b8d914f4fb/00e155989221056fb74efd6f967939f0.pdf) |
| 化学 | G10-G12 | 普通高中教科书·化学 必修1-2、选择性必修 | 上海科学技术出版社 | 2020秋 | [链接](https://edu.sh.gov.cn/cmsres/7a/7aed3909441a4c849d5d78b8d914f4fb/00e155989221056fb74efd6f967939f0.pdf) |
| 生物学 | G10-G12 | 普通高中教科书·生物学 必修1-2、选择性必修1-3（2020目录称生命科学） | 上海科学技术出版社 | 2020秋 | [链接](https://edu.sh.gov.cn/cmsres/7a/7aed3909441a4c849d5d78b8d914f4fb/00e155989221056fb74efd6f967939f0.pdf) |
| 地理 | G10-G12 | 普通高中教科书·地理 必修第一册、选择性必修1-3 | 中华地图学社 | 2020秋 | [链接](https://edu.sh.gov.cn/cmsres/7a/7aed3909441a4c849d5d78b8d914f4fb/00e155989221056fb74efd6f967939f0.pdf) |
| 信息技术 | G10-G12 | 普通高中教科书·信息技术 必修1、选择性必修1-6 | 华东师范大学出版社 | 2020秋 | [链接](https://edu.sh.gov.cn/cmsres/7a/7aed3909441a4c849d5d78b8d914f4fb/00e155989221056fb74efd6f967939f0.pdf) |
| 艺术/音乐/美术/体育与健康 | G10-G12 | 普通高中教科书 | 沪教育（艺术、体育）/沪音乐/沪书画 | 2020秋 | [链接](https://edu.sh.gov.cn/cmsres/7a/7aed3909441a4c849d5d78b8d914f4fb/00e155989221056fb74efd6f967939f0.pdf) |

### 国际课程

| 体系 | 年龄/阶段 | 科目 | 代码 | 来源 |
|---|---|---|---|---|
| Cambridge Primary | 5-11岁 | English / ESL / Mathematics / Science / Computing / Digital Literacy / Global Perspectives / Humanities | 0058 / 0057 / 0096 / 0097 / 0059 / 0072 / 0838 / 0065 | [链接](https://www.cambridgeinternational.org/programmes-and-qualifications/cambridge-primary/curriculum/) |
| Cambridge Lower Secondary | Stage 7-9（约11-14岁） | English / Mathematics / Science / ESL / Computing / Global Perspectives / Humanities | 0861 / 0862 / 0893 / 0876 / 0860 / 1129 / 0839 | [链接](https://www.cambridgeinternational.org/programmes-and-qualifications/cambridge-lower-secondary/curriculum/) |
| Cambridge Lower Secondary Checkpoint | Stage 9 | English, ESL, Mathematics, Science, Global Perspectives | 未列代码 | [链接](https://help.cambridgeinternational.org/hc/en-gb/articles/360000054998-Which-subjects-are-offered-for-Cambridge-Lower-Secondary-Checkpoint) |
| Cambridge IGCSE | 14-16岁 | Mathematics / International Mathematics / Additional Mathematics | 0580 / 0607 / 0606 | [链接](https://www.cambridgeinternational.org/Images/what-syllabuses-are-available.pdf) |
| Cambridge IGCSE | 14-16岁 | Physics / Chemistry / Biology / Combined Science / Co-ordinated Sciences (Double) | 0625 / 0620 / 0610 / 0653 / 0654 | [链接](https://www.cambridgeinternational.org/Images/what-syllabuses-are-available.pdf ; https://www.cambridgeinternational.org/Images/764332-2029-syllabus.pdf) |
| Cambridge IGCSE | 14-16岁 | English First Language / ESL (Speaking Endorsement) / ESL (Count-in Speaking) / Literature in English | 0500 / 0510 / 0511 / 0475 | [链接](https://www.cambridgeinternational.org/Images/637160-2024-2026-syllabus.pdf ; https://www.cambridgeinternational.org/Images/721343-2027-2029-syllabus.pdf) |
| Cambridge IGCSE | 14-16岁 | Chinese First Language / Chinese Second Language / Mandarin Chinese Foreign Language | 0509 / 0523 / 0547 | [链接](https://www.cambridgeinternational.org/Images/what-syllabuses-are-available.pdf) |
| Cambridge IGCSE | 14-16岁 | Computer Science / ICT / Economics / Business Studies / Geography / History / Global Perspectives | 0478 / 0417 / 0455 / 0450 / 0460 / 0470 / 0457 | [链接](https://www.cambridgeinternational.org/Images/what-syllabuses-are-available.pdf) |
| Cambridge AS & A Level | 16-19岁 | Mathematics / Further Mathematics / Physics / Chemistry / Biology / Computer Science / Economics | 9709 / 9231 / 9702 / 9701 / 9700 / 9618 / 9708 | [链接](https://www.cambridgeinternational.org/Images/what-syllabuses-are-available.pdf ; https://www.cambridgeinternational.org/programmes-and-qualifications/cambridge-international-as-and-a-level-computer-science-9618/) |
| IB PYP | 3-12岁 | 超学科框架 | - | [链接](https://www.ibo.org/programmes/primary-years-programme/key-facts-about-the-pyp/) |
| IB MYP | 11-16岁 | 8学科组：Language & literature, Language acquisition, Individuals & societies, Sciences, Mathematics, Arts, PHE, Design；eAssessment + Personal Project | - | [链接](https://ibo.org/globalassets/new-structure/brochures-and-infographics/pdfs/1503-myp-factsheet-for-parents.pdf) |
| IB DP | 16-19岁 | 6学科组 + TOK/EE/CAS；Math AA / Math AI（2021首考）；Physics（新大纲2025首考） | SL 150h / HL 240h | [链接](https://www.ibo.org/programmes/diploma-programme/curriculum/ ; https://ibo.org/globalassets/new-structure/recognition/pdfs/dp_sciences_physics_subject-brief_jan_2022_e.pdf) |
| AP (College Board) | 高中 | Calculus AB/BC, Precalculus, Statistics, CS A, CSP, Biology, Chemistry, Physics 1/2/C, English Lang/Lit, Chinese Language & Culture 等 | - | [链接](https://apcentral.collegeboard.org/courses) |
| Common Core (CCSS) | K-12 | Mathematics, English Language Arts | - | [链接](https://www.thecorestandards.org/about-the-standards/) |
| 民办学校实例（上海浦东新区民办惠立学校） | G8（2025学年第一学期） | 国家课程科目：语文、数学、英语、物理、化学、生物、历史、地理、道德与法治、信息科技等；未见IGCSE | - | [链接](https://cmsstatic.wellingtoncollege.cn/huili_school_shanghai/G8.pdf) |
