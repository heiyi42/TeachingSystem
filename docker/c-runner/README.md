# C 程序与安全实验评测镜像

在项目根目录构建：

```sh
docker build -t teaching-c-runner:1 docker/c-runner
```

保持 Docker 运行，然后启动原有后端。运行期间不自动下载镜像；Docker 或镜像不可用时返回可重试错误，不直接在宿主机执行学生程序。可用 `C_PROGRAM_IMAGE` 指定已构建的镜像标签或摘要。

编译与每个测试用例分别启动容器。运行容器只挂载本次编译的可执行文件，挂载为只读；不挂载项目目录、用户目录、`.env` 或 Docker 套接字。编译只挂载本次只读源码与独立可写编译输出目录。容器使用非 root 用户、只读根目录、禁网、删除全部 capabilities、禁止提权，保留 Docker 默认 seccomp。

运行限制：64 MiB 内存及等额 memory-swap（不增加交换空间）、1 个 CPU 配额、16 个进程、CPU 时间 3 秒、墙钟时间 5 秒、每个输出流最多读取 64 KiB。编译内存 256 MiB、墙钟 20 秒、文件上限 8 MiB。临时文件系统 16 MiB 且不可执行。程序超时、输出过量或异常退出后停止后续测试；无论成功或失败都清理本次容器。服务最多允许 2 个评测并发。

支持 C11 单文件、固定接口驱动与多文件共同编译链接。镜像还包含 Python3 和 OpenSSL，用于六类安全实验。安全脚本每轮重新运行，输入只读，产物写在 16 MiB tmpfs；最多导出 8 个普通文件、合计 32 KiB，拒绝符号链接与目录。独立容器核验导出产物，前端可下载产物。安全实验墙钟限时 15 秒，其余隔离限制沿用；固定校验器按宿主映射 UID 读取重新落盘的产物。通过表示给定测试集通过，不证明所有输入均正确。Docker 隔离与资源限制是本地教学评测的基础；部署到公网时仍应使用专门的评测主机和访问控制。

运行 Docker 集成验收（测试数据与正式聊天隔离）：

```sh
TEST_C_PROGRAM_DOCKER=1 WEB_CHAT_STORE_PATH=./tmp/c_runner_test_chats.json python -m unittest tests.learning.test_learning_expansion tests.learning.test_learning_coverage
```
