from __future__ import annotations

from .learning_courses import course_chapters

# 固定驱动负责输入、边界和资源检查，学生只提交指定接口的实现。
TASKS = {
    "reverse": (
        "指针区间反转",
        9,
        "c_program_pointer",
        "void reverse(int *a, size_t n);",
        "原地反转 n 个整数，n=0 时允许 a=NULL。",
        "void reverse(int *a,size_t n){for(size_t i=0;i<n/2;i++){int t=a[i];a[i]=a[n-1-i];a[n-1-i]=t;}}",
    ),
    "matrix": (
        "数组指针求和",
        9,
        "c_program_pointer",
        "long long matrix_sum(size_t n, const int (*a)[3]);",
        "通过数组指针计算 n×3 矩阵元素和；n=0 时 a=NULL。",
        "long long matrix_sum(size_t n,const int (*a)[3]){long long s=0;for(size_t i=0;i<n;i++)for(size_t j=0;j<3;j++)s+=a[i][j];return s;}",
    ),
    "callback": (
        "函数指针筛选",
        9,
        "c_program_pointer",
        "size_t count_if(const int *a, size_t n, int (*predicate)(int));",
        "调用传入的谓词，统计返回非零的元素。驱动分别传入正数、偶数、负数谓词。",
        "size_t count_if(const int *a,size_t n,int (*predicate)(int)){size_t c=0;for(size_t i=0;i<n;i++)if(predicate(a[i]))c++;return c;}",
    ),
    "clone": (
        "二级指针与堆数组",
        10,
        "c_program_memory",
        "int clone_array(const int *a, size_t n, int **out);",
        "空数组成功且 *out=NULL；非空数组用 malloc 复制，返回0；分配失败返回-1且 *out=NULL。成功后调用方释放数组。",
        "int clone_array(const int *a,size_t n,int **out){*out=NULL;if(!n)return 0;int *p=malloc(n*sizeof *p);if(!p)return -1;for(size_t i=0;i<n;i++)p[i]=a[i];*out=p;return 0;}",
    ),
    "append": (
        "realloc 失败与所有权",
        10,
        "c_program_memory",
        "int append_value(int **a, size_t *n, int value);",
        "原数组由 malloc 分配，扩容追加 value，成功返回0并更新地址和长度；realloc 失败返回-1且原指针、长度、数据保持不变。不能提前释放旧地址。",
        "int append_value(int **a,size_t *n,int value){int *p=realloc(*a,(*n+1)*sizeof *p);if(!p)return -1;p[*n]=value;*a=p;(*n)++;return 0;}",
    ),
    "filter": (
        "动态结果与释放",
        10,
        "c_program_memory",
        "int positive_copy(const int *a, size_t n, int **out, size_t *count);",
        "按原顺序复制严格正数；无正数成功且 *out=NULL,*count=0，不分配；否则 malloc 分配，失败返回-1且输出为空，成功返回0。调用方释放结果。",
        "int positive_copy(const int *a,size_t n,int **out,size_t *count){*out=NULL;*count=0;size_t k=0;for(size_t i=0;i<n;i++)if(a[i]>0)k++;if(!k)return 0;int *p=malloc(k*sizeof *p);if(!p)return -1;k=0;for(size_t i=0;i<n;i++)if(a[i]>0)p[k++]=a[i];*out=p;*count=k;return 0;}",
    ),
    "file_sum": (
        "文本文件与错误返回",
        13,
        "c_program_file",
        "int sum_file(const char *path, long long *out);",
        "文件含空白分隔整数（-1000至1000），计算和。空文件成功；打开失败或出现非整数返回-1，成功返回0并写 *out。必须关闭文件。",
        'int sum_file(const char *path,long long *out){FILE *f=fopen(path,"r");if(!f)return -1;long long s=0;int x,r;while((r=fscanf(f,"%d",&x))==1)s+=x;int ok=r==EOF&&!ferror(f);int closed=fclose(f)==0;if(!ok||!closed)return -1;*out=s;return 0;}',
    ),
    "file_copy": (
        "二进制文件复制",
        13,
        "c_program_file",
        "int copy_file(const char *src, const char *dst);",
        "逐字节复制文件，保留 NUL 和0xff；任何打开、读、写、关闭错误返回-1，成功返回0。源不存在时不得返回成功。",
        'int copy_file(const char *src,const char *dst){FILE *a=fopen(src,"rb");if(!a)return -1;FILE *b=fopen(dst,"wb");if(!b){fclose(a);return -1;}int c,ok=1;while((c=fgetc(a))!=EOF)if(fputc(c,b)==EOF){ok=0;break;}if(ferror(a))ok=0;if(fclose(a))ok=0;if(fclose(b))ok=0;return ok?0:-1;}',
    ),
    "file_append": (
        "追加记录与文件保持",
        13,
        "c_program_file",
        "int append_record(const char *path, int value);",
        "在现有文本末尾追加 value 和一个换行；不得截断原内容。若文件不存在则创建。任何I/O错误返回-1，成功返回0。",
        'int append_record(const char *path,int value){FILE *f=fopen(path,"a");if(!f)return -1;int ok=fprintf(f,"%d\\n",value)>=0;int closed=fclose(f)==0;return ok&&closed?0:-1;}',
    ),
    "module_sum": (
        "头文件与数组模块",
        15,
        "c_program_module",
        "long long aggregate(const int *a, size_t n);",
        "分别提交 main.c、stats.c 和 stats.h；main读取 n 和 n 个整数，调用 stats.c 的 aggregate 输出和。n=0至100。stats.h 声明接口并有包含保护。",
        "long long aggregate(const int *a,size_t n){long long s=0;for(size_t i=0;i<n;i++)s+=a[i];return s;}",
    ),
    "module_range": (
        "跨文件极差计算",
        15,
        "c_program_module",
        "long long aggregate(const int *a, size_t n);",
        "分别提交 main.c、stats.c 和 stats.h；main调用 aggregate 输出最大值减最小值；空数组返回0。n=0至100。使用头文件共享声明。",
        "long long aggregate(const int *a,size_t n){if(!n)return 0;int lo=a[0],hi=a[0];for(size_t i=1;i<n;i++){if(a[i]<lo)lo=a[i];if(a[i]>hi)hi=a[i];}return (long long)hi-lo;}",
    ),
    "module_positive": (
        "模块接口与过滤统计",
        15,
        "c_program_module",
        "long long aggregate(const int *a, size_t n);",
        "分别提交 main.c、stats.c 和 stats.h；main调用 aggregate 输出严格正数的和。n=0至100，空数组返回0。使用头文件共享声明。",
        "long long aggregate(const int *a,size_t n){long long s=0;for(size_t i=0;i<n;i++)if(a[i]>0)s+=a[i];return s;}",
    ),
}

HEADER = "#include <stddef.h>\n#include <stdio.h>\n#include <stdlib.h>\n"
MAIN = '#include "stats.h"\n#include <stdio.h>\nint main(void){size_t n;int a[100];if(scanf("%zu",&n)!=1||n>100)return 1;for(size_t i=0;i<n;i++)if(scanf("%d",&a[i])!=1)return 1;printf("%lld\\n",aggregate(a,n));return 0;}\n'


def build_advanced_program_exercises():
    result = {}
    for task, (title, chapter, family, prototype, description, _) in TASKS.items():
        section = next(
            c for c in course_chapters("C_program") if c["number"] == chapter
        )
        module = task.startswith("module_")
        files = ["main.c", "stats.c", "stats.h"] if module else ["student.c"]
        starter = (
            [
                '#include "stats.h"\n#include <stdio.h>\nint main(void){return 0;}\n',
                '#include "stats.h"\n',
                "#ifndef STATS_H\n#define STATS_H\n#include <stddef.h>\n"
                + prototype
                + "\n#endif\n",
            ]
            if module
            else [HEADER + prototype + "\n"]
        )
        key = "c_advanced_" + task
        result[key] = dict(
            id=key,
            subject_id="C_program",
            subject_name="C 语言",
            chapter_id=section["id"],
            chapter_title=section["title"],
            title="程序实验 · " + title,
            algorithm={
                "c_program_pointer": "指针进阶程序",
                "c_program_memory": "动态内存程序",
                "c_program_file": "文件操作程序",
                "c_program_module": "多文件程序",
            }[family],
            family_id=family,
            kind="c_program",
            difficulty="进阶",
            parameters=dict(
                program_task=task,
                checkpoints=files,
                source_files=files,
                starter_files=starter,
                starter=starter[0],
                code="",
                family="program",
                description=description
                + " 输入规模不超过100，元素范围[-1000,1000]。"
                + (
                    "驱动通过替代分配器检查分配失败、释放和所有权，禁止自定义 malloc/free/realloc 或直接调用底层分配器。"
                    if family == "c_program_memory"
                    else ""
                ),
                hint={
                    "c_program_pointer": "核对指针步长、空区间和函数指针调用；不要假定谓词只检查正数。",
                    "c_program_memory": "用临时指针接收分配结果，失败时保持约定的输出状态；谁分配、谁负责释放要明确。",
                    "c_program_file": "检查 fopen 和每次读写的返回值；二进制数据不能用 strlen，追加模式不能用 w。",
                    "c_program_module": "头文件声明应与实现一致；main.c 和 stats.c 一起编译并链接，头文件不要重复定义函数。",
                }[family],
            ),
            rules="按给定接口提交 C11 源文件，驱动和测试数据由评测提供；多文件题共同编译并链接。网络关闭，运行内存64 MiB，CPU3秒，墙钟5秒；测试通过只表示给定测试集通过。",
            training_tags=[
                "compile_error",
                "wrong_output",
                "runtime_error",
                "time_limit",
            ],
        )
    return result


def advanced_solution(task):
    prototype, code = TASKS[task][3], TASKS[task][5]
    if task.startswith("module_"):
        return [
            MAIN,
            '#include "stats.h"\n' + code + "\n",
            "#ifndef STATS_H\n#define STATS_H\n#include <stddef.h>\n"
            + prototype
            + "\n#endif\n",
        ]
    return [HEADER + code + "\n"]


def advanced_cases(task):
    arrays = [[], [0], [-7], [-3, 0, 8, 8, -9], [1000, -1000, 1], list(range(-50, 50))]
    result = []
    for a in arrays:
        text = str(len(a)) + " " + " ".join(map(str, a)) + "\n"
        if task.startswith("module_"):
            value = (
                sum(a)
                if task == "module_sum"
                else (
                    (max(a) - min(a) if a else 0)
                    if task == "module_range"
                    else sum(x for x in a if x > 0)
                )
            )
            result.append((text, str(value)))
        else:
            result.append((text, "OK"))
    return result


def advanced_driver(task):
    if task.startswith("module_"):
        return None
    body = {
        "reverse": "reverse(n?a:NULL,n);for(size_t i=0;i<n;i++)if(a[i]!=original[n-1-i])return 2;",
        "matrix": "int m[100][3];long long s=0;for(size_t i=0;i<n;i++)for(int j=0;j<3;j++){m[i][j]=a[i]+j;s+=m[i][j];}if(matrix_sum(n,n?(const int (*)[3])m:NULL)!=s)return 2;",
        "callback": "size_t pos=0,even=0,neg=0;for(size_t i=0;i<n;i++){pos+=a[i]>0;even+=a[i]%2==0;neg+=a[i]<0;}if(count_if(n?a:NULL,n,is_positive)!=pos||count_if(n?a:NULL,n,is_even)!=even||count_if(n?a:NULL,n,is_negative)!=neg)return 2;",
        "clone": "for(int mode=0;mode<2;mode++){fail_alloc=mode;int *p=(int*)1;int r=clone_array(n?a:NULL,n,&p);if(mode&&n){if(r!=-1||p!=NULL)return 2;}else{if(r||(!n&&p)|| (n&&(!p||p==a)))return 2;for(size_t i=0;i<n;i++)if(p[i]!=a[i])return 2;tracked_free(p);}if(live_count)return 3;}",
        "append": "for(int mode=0;mode<2;mode++){fail_alloc=0;int *p=n?tracked_malloc(n*sizeof(int)):NULL;for(size_t i=0;i<n;i++)p[i]=a[i];int *old=p;size_t k=n;fail_alloc=mode;int r=append_value(&p,&k,17);if(mode){if(r!=-1||p!=old||k!=n)return 2;}else{if(r||!p||k!=n+1||p[n]!=17)return 2;}for(size_t i=0;i<n;i++)if(p[i]!=a[i])return 2;fail_alloc=0;tracked_free(p);if(live_count)return 3;}",
        "filter": "size_t expected=0;for(size_t i=0;i<n;i++)expected+=a[i]>0;for(int mode=0;mode<2;mode++){fail_alloc=mode;int *p=(int*)1;size_t k=99;int r=positive_copy(n?a:NULL,n,&p,&k);if(mode&&expected){if(r!=-1||p||k)return 2;}else{if(r||k!=expected||(!k&&p)||(k&&(!p||p==a)))return 2;size_t j=0;for(size_t i=0;i<n;i++)if(a[i]>0&&p[j++]!=a[i])return 2;tracked_free(p);}if(live_count)return 3;}",
        "file_sum": 'long long got,s=0;FILE *f=fopen("/tmp/numbers.txt","w");if(!f)return 4;for(size_t i=0;i<n;i++){fprintf(f,"%d\\n",a[i]);s+=a[i];}fclose(f);for(int repeat=0;repeat<80;repeat++)if(sum_file("/tmp/numbers.txt",&got)||got!=s)return 2;if(sum_file("/tmp/missing",&got)!=-1)return 2;f=fopen("/tmp/numbers.txt","w");fputs("1 invalid 2",f);fclose(f);if(sum_file("/tmp/numbers.txt",&got)!=-1)return 2;',
        "file_copy": 'FILE *f=fopen("/tmp/source.bin","wb");if(!f)return 4;for(size_t i=0;i<n;i++){fputc((unsigned char)a[i],f);fputc(0,f);fputc(255,f);}fclose(f);for(int repeat=0;repeat<80;repeat++)if(copy_file("/tmp/source.bin","/tmp/copy.bin"))return 2;f=fopen("/tmp/copy.bin","rb");if(!f)return 2;for(size_t i=0;i<n;i++)if(fgetc(f)!=(unsigned char)a[i]||fgetc(f)!=0||fgetc(f)!=255)return 2;if(fgetc(f)!=EOF)return 2;fclose(f);if(copy_file("/tmp/missing","/tmp/copy.bin")!=-1||copy_file("/tmp/source.bin","/missing/target")!=-1)return 2;',
        "file_append": 'FILE *f=fopen("/tmp/log.txt","w");if(!f)return 4;fputs("original\\n",f);fclose(f);for(size_t i=0;i<n;i++)if(append_record("/tmp/log.txt",a[i]))return 2;f=fopen("/tmp/log.txt","r");char line[32];if(!fgets(line,sizeof line,f)||strcmp(line,"original\\n"))return 2;for(size_t i=0;i<n;i++){int v;if(fscanf(f,"%d",&v)!=1||v!=a[i])return 2;}fclose(f);if(append_record("/missing/log",1)!=-1)return 2;if(append_record("/tmp/new.txt",17))return 2;f=fopen("/tmp/new.txt","r");int v;if(!f||fscanf(f,"%d",&v)!=1||v!=17)return 2;fclose(f);',
    }[task]
    allocator = r"""
static int fail_alloc=0,live_count=0;static void *live[8];
static void *tracked_malloc(size_t n){if(fail_alloc)return NULL;void *p=malloc(n);if(p){if(live_count==8)exit(3);live[live_count++]=p;}return p;}
static void tracked_free(void *p){if(!p)return;int i;for(i=0;i<live_count;i++)if(live[i]==p)break;if(i==live_count)exit(3);free(p);live[i]=live[--live_count];}
void *tracked_realloc(void *p,size_t n){if(fail_alloc)return NULL;if(!p)return tracked_malloc(n);int i;for(i=0;i<live_count;i++)if(live[i]==p)break;if(i==live_count)exit(3);void *q=realloc(p,n);if(q)live[i]=q;return q;}
#define malloc tracked_malloc
#define free tracked_free
#define realloc tracked_realloc
"""
    memory = TASKS[task][2] == "c_program_memory"
    return (
        HEADER
        + "#include <string.h>\n"
        + (allocator if memory else "")
        + '\n#include "student.c"\n'
        + ("#undef malloc\n#undef free\n#undef realloc\n" if memory else "")
        + (
            "static int is_positive(int x){return x>0;}static int is_even(int x){return x%2==0;}static int is_negative(int x){return x<0;}\n"
            if task == "callback"
            else ""
        )
        + "int main(void){size_t n;int a[100];"
        + ("int original[100];" if task == "reverse" else "")
        + 'if(scanf("%zu",&n)!=1||n>100)return 1;for(size_t i=0;i<n;i++){if(scanf("%d",&a[i])!=1)return 1;'
        + ("original[i]=a[i];" if task == "reverse" else "")
        + "}\n"
        + body
        + '\nputs("OK");return 0;}\n'
    )
