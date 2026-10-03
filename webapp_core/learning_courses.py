from __future__ import annotations


COURSES = {
    "C_program": {
        "name": "C 语言",
        "capabilities": ["课程问答", "代码分析与编译诊断", "程序跟踪、单文件与多文件程序实验"],
        "planned": "已开放循环、数组指针、函数传参跟踪及受限返回语句订正；支持循环、数组、字符串、进阶指针、动态内存和文件程序隔离评测，以及多文件共同编译链接。",
        "chapters": [
            "入门与程序结构", "类型系统与变量", "运算符与表达式体系", "流程控制",
            "函数与作用域", "数组基础", "字符串与字符处理", "指针入门", "指针进阶",
            "动态内存管理", "结构体与联合体", "预处理器与宏", "文件 I/O",
            "错误处理与调试", "数据结构与工程模块化", "标准库进阶与最佳实践",
        ],
        "first_chapter": 1,
    },
    "operating_systems": {
        "name": "操作系统",
        "capabilities": ["课程问答", "FIFO/LRU/OPT 页面置换与 FCFS/RR/SJF/SRTF 调度训练", "银行家算法安全性检查训练"],
        "planned": "已接 CLOCK 槽位、访问位与指针跟踪及分页地址转换，磁盘 FCFS/SSTF/LOOK；新增资源请求判断和四种调度的完成、周转、等待、响应时间训练；新增 PV 阻塞唤醒、互斥、生产者消费者与哲学家进餐有限模型核验；新增读者优先、写者优先与公平排队的状态推演；已补进程状态、线程资源归属及连续、链接、索引文件分配训练。",
        "chapters": [
            "导读", "计算机系统概述", "操作系统概述", "进程描述与控制", "线程",
            "互斥与同步", "死锁与饥饿", "内存管理", "虚拟内存", "单处理器调度",
            "多处理器与实时调度", "I/O 与磁盘调度", "文件管理", "嵌入式操作系统",
            "计算机安全威胁", "计算机安全技术", "分布式处理与集群",
        ],
        "first_chapter": 0,
    },
    "cybersec_lab": {
        "name": "网络安全实验",
        "capabilities": ["课程问答", "Python 实验脚本与真实产物校验", "DH 计算、角色权限矩阵、模拟日志、认证流程与教学签名验证训练"],
        "planned": "已开放隔离数据库、OpenSSL 文件加密与标准签名、PBKDF2 凭据与HTTP登录、Linux权限、真实服务日志审计实验；原有模拟题保留为基础训练。每轮环境重建、产物独立校验，支持实验报告和材料提交。",
        "chapters": ["导言", "网络数据库设计", "加密机制", "认证机制", "访问控制", "数字签名", "安全审计"],
        "first_chapter": 0,
    },
}


def course_chapters(subject_id: str) -> list[dict]:
    course = COURSES[subject_id]
    return [
        {"id": f"{subject_id}_{number:02d}", "number": number, "title": title}
        for number, title in enumerate(course["chapters"], start=course["first_chapter"])
    ]
