# SEED Emulator Docker → K8s 迁移记录

## 已完成迁移的示例

| 示例 | K8s文件 | 节点数 | 状态 | 日期 | 备注 |
|------|---------|--------|------|------|------|
| A00_simple_as | k8s_simple_as.py | 16 | ✅ 编译+部署验证 | 2026-02 | 周子为原版 |
| A01_transit_as | k8s_transit_as.py | - | ✅ 编译+部署验证 | 2026-02 | 周子为原版 |
| A20_nano_internet | k8s_nano_internet.py | 14 | ✅ 编译+部署+跨IX验证 | 2026-02~03 | 修复了peering关系问题(PR#459) |
| B00_mini_internet | k8s_mini_internet.py | - | ✅ 编译+部署验证 | 2026-02 | 周子为原版 |
| B01_dns_component | k8s_dns_component.py | 27 | ✅ 编译+部署验证 | 2026-02 | DNS全层级验证通过 |
| B20_dhcp | k8s_dhcp.py | 16 | ✅ 编译+部署验证 | 2026-02 | 4个DHCP客户端获取IP |
| B21_etc_hosts | k8s_etc_hosts.py | 25 | ✅ 编译+dry-run | 2026-03-14 | EtcHosts注入验证OK |
| B02_mini_internet_with_dns | k8s_mini_internet_with_dns.py | - | ✅ 编译+dry-run | 2026-03-14 | DNS层内联，261行 |
| A11_add_containers_new | k8s_add_containers_new.py | 7+2 | ✅ 编译+dry-run | 2026-03-14 | attachCustomContainer→辅助函数 |
| Y01_bgp_prefix_hijacking | k8s_bgp_hijacking.py | - | ⚠️ 待验证 | 2026-02 | 早期版本，需确认能否跑通 |

## 发现并修复的Bug

### 1. Bridge命名超限 (已修复, PR#455已合并)
- **问题**: Linux要求网桥名≤15字符，KubernetesCompiler生成的名称超限
- **修复**: `_safeBridgeName()` 方法，用MD5 hash截断
- **影响**: 所有多网络示例

### 2. KubernetesCompiler Import错误 (已修复, PR#455已合并)
- **问题**: 示例文件import路径和构造函数参数不正确
- **修复**: 修正import语句和参数

### 3. 跨IX路由不通 (已修复, PR#459待review)
- **问题**: k8s_nano_internet用addRsPeer(Peer关系)，路由标记PEER_COMM被valley-free过滤
- **根因**: Peer模式下跨IX不通是正确的BGP行为；示例缺少Ibgp和Ospf层
- **修复**: 改用addPrivatePeering(Provider)，添加Ibgp+Ospf层，与Docker版A20一致

## 跳过的示例

| 示例 | 原因 |
|------|------|
| A05_components | 多文件组件化教学演示(saveComponent/loadComponent)，不适合K8s迁移 |
| A02_transit_as_mpls | MPLS层K8s不支持 |
| A03_real_world | OpenVPN真实网络接入，K8s网络策略复杂 |
| A07_compilers | 编译器对比演示，概念不同 |
| A08_buildtime_docker | Docker编译期专属特性 |
| SCION系列(S01-S08) | KubernetesCompiler不支持SCION层 |
| Blockchain系列(D00-D60) | 需PersistentVolume+大量资源，杜老师确认不用做 |
| Hybrid系列(B03-B05,B50) | 需真实网络接入 |

## 待迁移示例

### P1 中等难度
- A06_merge_emulation
- A09_node_customization
- A21_shadow_internet
- B22_botnet
- B23_darknet_tor
- B24_ip_anycast
- B25_pki
- B26_ipfs_kubo
- B28_traffic_generator
- B29_email_dns
- Y03_mirai
