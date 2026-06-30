import requests
import re
import os
import time
import sys
import io
import socket
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ============================================
# 编码与输出兼容性配置 (防止 Windows 控制台 UnicodeEncodeError)
# ============================================
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        # 兼容旧版本 Python
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# ============================================
# 基础配置
# ============================================
prefer_port = True  # ✅ 是否优先显示带端口的 IP（True=带端口排前）
urls = [
    'https://api.uouin.com/cloudflare.html',
    'https://ip.164746.xyz',
    'https://cf.090227.xyz'
]

zip_data_url = "https://zip.cm.edu.kg/all.txt"
zip_target_regions = ["SG", "JP", "HK", "US", "AU"]
zip_count_per_region = 20

# ✅ 改进的 IP+端口匹配正则
ip_pattern = r'\d{1,3}(?:\.\d{1,3}){3}(?::\d{1,5})?'

# ============================================
# GitHub 多源配置
# ============================================
github_sources = [
    "https://raw.githubusercontent.com/JiangXi9527/CNJX/refs/heads/main/test-ip.txt"
]
github_targets = {
    "SG": 20,
    "JP": 10,
    "HK": 20,
    "Los Angeles": 20
}

# ============================================
# 测速配置 (响应速度优先)
# ============================================
max_workers = 100        # ✅ 并发测速的线程数
speed_timeout = 1.5      # ✅ 测速超时时间（秒）
max_latency_limit = 500 # ✅ 允许的最大延迟（毫秒），超过此值的 IP 将被过滤掉

# ============================================
# 全局 requests Session（带重试）
# ============================================
session = requests.Session()
retries = Retry(
    total=3,  # 重试次数
    backoff_factor=2,  # 每次重试延迟递增
    status_forcelist=[429, 500, 502, 503, 504],
)
adapter = HTTPAdapter(max_retries=retries)
session.mount("https://", adapter)
session.mount("http://", adapter)

def safe_get(url, timeout=(5, 30)):
    """带容错与重试的请求函数"""
    try:
        resp = session.get(url, timeout=timeout)
        resp.raise_for_status()
        return resp
    except requests.exceptions.Timeout:
        print(f"⏰ 请求超时: {url}")
    except requests.exceptions.RequestException as e:
        print(f"❌ 请求失败: {url} | 原因: {e}")
    return None

# ============================================
# 并发 TCP 延迟测试逻辑
# ============================================
def test_single_ip(ip, timeout=1.5):
    """测试单个 IP 的 TCP 连接延迟（毫秒）"""
    if ":" in ip:
        ip_only, port_str = ip.split(":")
        try:
            port = int(port_str)
        except ValueError:
            port = 443
    else:
        ip_only = ip
        port = 443

    start_time = time.perf_counter()
    try:
        # 使用 TCP 三次握手测试延迟
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((ip_only, port))
        sock.close()
        latency = (time.perf_counter() - start_time) * 1000  # 转换为毫秒
        return ip, latency
    except Exception:
        return ip, None

def test_all_ips_latency(ips, max_workers=100, timeout=1.5):
    """并发测试所有 IP 的延迟，过滤掉不可达和超时的 IP"""
    print(f"⚡ 开始对 {len(ips)} 个 IP 进行并发测速...")
    latencies = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(test_single_ip, ip, timeout): ip for ip in ips}
        for future in as_completed(futures):
            ip, latency = future.result()
            if latency is not None and latency <= max_latency_limit:
                latencies[ip] = latency
    print(f"✅ 测速完成，有效在线 IP 数量: {len(latencies)} 个 (延迟上限: {max_latency_limit}ms)")
    return latencies

# ============================================
# 从 zip.cm.edu.kg 获取地区数据
# ============================================
def fetch_zip_region_ips(url, regions, n_each=50):
    print(f"正在从 {url} 获取指定地区数据...")
    resp = safe_get(url, timeout=(5, 40))
    if not resp:
        print(f"⚠️ 无法访问 {url}，跳过该数据源。")
        return {r: [] for r in regions}

    lines = resp.text.splitlines()

    region_keys = {
        "SG": ["SG", "Singapore", "新加坡"],
        "JP": ["JP", "Japan", "日本"],
        "HK": ["HK", "Hong Kong", "香港"],
        "US": ["US", "United States", "美国"],
        "AU": ["AU", "Australia", "澳大利亚"],
    }

    results = {r: [] for r in regions}

    def belongs_region(line, keys):
        return any(k.lower() in line.lower() for k in keys)

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        for region, keys in region_keys.items():
            if region in regions and belongs_region(stripped, keys):
                m = re.search(ip_pattern, stripped)
                if m and len(results[region]) < n_each:
                    results[region].append(m.group(0))
                break
        if all(len(results[r]) >= n_each for r in regions):
            break

    print("✅ 获取完毕：")
    for r in regions:
        print(f"  {r}: {len(results[r])} 条")
    return results

# ============================================
# 从多个 GitHub 源提取各地区 IP
# ============================================
def fetch_github_region_ips(sources, targets):
    print(f"正在从 GitHub 源获取多地区 IP（含端口）...")
    results = {r: [] for r in targets.keys()}
    region_keys = {
        "SG": ["SG", "Singapore", "新加坡"],
        "JP": ["JP", "Japan", "日本"],
        "HK": ["HK", "Hong Kong", "香港"],
        "Los Angeles": ["Los Angeles", "洛杉矶"],
    }

    for src in sources:
        print(f"🔹 检索源: {src}")
        resp = safe_get(src)
        if not resp:
            continue

        lines = resp.text.splitlines()
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            for region, keys in region_keys.items():
                if region not in targets:
                    continue
                if any(k.lower() in stripped.lower() for k in keys):
                    m = re.search(ip_pattern, stripped)
                    if m and len(results[region]) < targets[region]:
                        results[region].append(m.group(0))
                        break
        time.sleep(0.3)

    for r, ips in results.items():
        print(f"✅ {r}: 共获取 {len(ips)} 个 IP（含端口）")
    return results

# ============================================
# 缓存系统 (载入历史已解析 IP 信息)
# ============================================
cache = {}
if os.path.exists("ip.txt"):
    with open("ip.txt", "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if "#" in line:
                parts = line.split("#")
                if len(parts) == 3:
                    ip, location, isp = parts
                    if "-" in location:
                        location = location.split("-")[0]
                    cache[ip] = f"{location}#{isp}"
                elif len(parts) == 2:
                    ip, location = parts
                    if "-" in location:
                        location = location.split("-")[0]
                    cache[ip] = f"{location}#未知ISP"

# ============================================
# 普通网页源抓取
# ============================================
ip_set = set()
for url in urls:
    resp = safe_get(url)
    if not resp:
        continue
    html_text = resp.text
    ip_matches = re.findall(ip_pattern, html_text)
    ip_set.update(ip_matches)
    print(f"✅ 从 {url} 抓取到 {len(ip_matches)} 个 IP（含端口）")

# ============================================
# 添加 zip.cm.edu.kg 数据
# ============================================
zip_region_ips = fetch_zip_region_ips(zip_data_url, zip_target_regions, zip_count_per_region)
for region, ips in zip_region_ips.items():
    for ip in ips:
        ip_set.add(ip)
        # 为 zip 源建立基础缓存（避免查 API）
        cache[ip] = f"{region}#zip.cm.edu.kg"

# ============================================
# 添加 GitHub 多源数据
# ============================================
github_region_ips = fetch_github_region_ips(github_sources, github_targets)
for region, ips in github_region_ips.items():
    for ip in ips:
        ip_set.add(ip)
        # 为 github 源建立基础缓存（避免查 API）
        cache[ip] = f"{region}#github"

# ============================================
# 步骤 1：对所有采集到的 IP 进行并发测速
# ============================================
latencies = test_all_ips_latency(ip_set, max_workers=max_workers, timeout=speed_timeout)

# 过滤掉无法连接或超时的 IP，仅保留有效在线 IP
reachable_ips = [ip for ip in ip_set if ip in latencies]

# ============================================
# 查询 IP 信息（仅对在线 IP 进行查询，并带频率限制）
# ============================================
def get_ip_info(ip):
    try:
        ip_no_port = ip.split(":")[0]
        r = safe_get(f"http://ip-api.com/json/{ip_no_port}?lang=zh-CN", timeout=(3, 8))
        if not r:
            return "查询失败#未知ISP"
        data = r.json()
        if data.get("status") == "success":
            location = f"{data.get('country', '')} {data.get('regionName', '')}".strip()
            isp = data.get("isp", "未知ISP")
            return f"{location}#{isp}"
        else:
            return "未知地区#未知ISP"
    except:
        return "查询失败#未知ISP"

results = {}
total_queries = 0
for ip in sorted(reachable_ips):
    if ip in cache:
        info = cache[ip]
    else:
        print(f"🔍 查询未缓存在线 IP 归属地: {ip}")
        info = get_ip_info(ip)
        total_queries += 1
        # ip-api.com 限制每分钟最多 45 次请求，故增加 1.5 秒延时
        time.sleep(1.5)
    results[ip] = info

# ============================================
# 按地区分组 + 延迟排序输出
# ============================================
grouped = defaultdict(list)
for ip in reachable_ips:
    info = results.get(ip, "未知地区#未知ISP")
    region, isp = info.split("#")
    latency = latencies[ip]
    grouped[region].append((ip, isp, latency))

with open("ip.txt", "w", encoding="utf-8") as f:
    for region in sorted(grouped.keys()):
        # 排序策略：
        # 1. 如果 prefer_port 为 True，则带端口的 IP 排在前面
        # 2. 在以上基础上，按延迟由低到高（升序）排列
        if prefer_port:
            sorted_ips = sorted(grouped[region], key=lambda x: (":" not in x[0], x[2]))
        else:
            sorted_ips = sorted(grouped[region], key=lambda x: x[2])

        for idx, (ip, isp, latency) in enumerate(sorted_ips, 1):
            # 每行输出格式：IP#名称，删除延迟和ISP后缀
            f.write(f"{ip}#{region}-{idx}\n")
        f.write("\n")

print(f"\n🎯 逻辑优化完毕！")
print(f"  - 总共测试了 {len(ip_set)} 个 IP 节点")
print(f"  - 筛选出在线且延迟低于 {max_latency_limit}ms 的优质 IP 节点 {len(reachable_ips)} 个")
print(f"  - 期间执行了 {total_queries} 次归属地 API 查询")
print(f"  - 结果已按【地区 + 响应速度（延迟）】进行排序，并保存至 ip.txt")
