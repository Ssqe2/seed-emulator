# SEED Emulator — WSL 环境 Vagrant 部署指南

## 背景

这份指南用于在 Windows WSL 环境中，通过 Vagrant + VirtualBox 自动创建虚拟机集群，
然后通过 SSH 在虚拟机上安装 K3s、Multus、CNI 插件和私有镜像仓库。

项目文件说明：
- scripts/seed_vagrant.sh  — 第一步脚本：读取 cluster.yaml，生成 Vagrantfile，创建 VM
- scripts/seed_k3s_setup.sh — 第二步脚本：SSH 到 VM 上安装 K3s 及所有组件
- configs/cluster.yaml      — VM 配置（provider、节点数量、IP、CPU、内存）
- configs/k3s.yaml          — K3s 配置（版本、网络、Multus、Registry）

## 前置条件

1. Windows 上已安装 VirtualBox
2. WSL（Ubuntu）已安装
3. WSL 里能运行 bash、ssh、python3

## 第一步：安装 Vagrant

在 WSL 里执行：

```bash
wget -O /tmp/vagrant.deb https://releases.hashicorp.com/vagrant/2.4.3/vagrant_2.4.3-1_amd64.deb
sudo dpkg -i /tmp/vagrant.deb
vagrant --version
# 应输出：Vagrant 2.4.3
```

注意：WSL 里的 Vagrant 需要能调用 Windows 上的 VirtualBox 或 VMware。
相关环境变量在**下一节**通过 `configs/env.sh` 配置，**不要**手动 export 或改 `~/.bashrc`。

## 第二步：安装 Python3 和 PyYAML

脚本需要 Python3 解析 YAML 配置文件：

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-pip
pip3 install pyyaml
```

## 第三步：配置环境（configs/env.sh）

脚本需要知道 WSL 下 Windows 上 VirtualBox/VMware 的路径。
**复制模板文件，编辑自己的那份，不要改模板：**

```bash
cd <你的 seed-emulator>/vagrant-deploy
cp configs/env.sh.example configs/env.sh
# 用编辑器打开 configs/env.sh，取消注释对应自己环境的行
```

WSL + VirtualBox（Windows 默认安装路径）：
```bash
export VAGRANT_WSL_ENABLE_WINDOWS_ACCESS="1"
export PATH="$PATH:/mnt/c/Program Files/Oracle/VirtualBox"
```

WSL + VMware Workstation（Windows 默认安装路径）：
```bash
export VAGRANT_WSL_ENABLE_WINDOWS_ACCESS="1"
export PATH="$PATH:/mnt/c/Program Files (x86)/VMware/VMware Workstation"
```

`configs/env.sh` 在 `.gitignore` 里，每人自己维护。
**原生 Linux / macOS 用户无需创建 env.sh**，脚本会自动跳过。

## 第四步：修改 cluster.yaml

文件位置：configs/cluster.yaml

改成：
```yaml
provider: virtualbox
box: ubuntu/jammy64

network:
  type: private_network

nodes:
  - name: master
    role: master
    cpus: 2
    memory: 4096
    ip: 192.168.56.10

  - name: worker1
    role: worker
    cpus: 2
    memory: 2048
    ip: 192.168.56.11

  - name: worker2
    role: worker
    cpus: 2
    memory: 2048
    ip: 192.168.56.12
```

IP 网段 192.168.56.x 是 VirtualBox host-only 网络的默认网段。
节点数量和资源可根据需要调整。

## 第五步：确认 configs/k3s.yaml

一般不需要改。如果你在**海外**或有直连 GitHub 的代理，
把 `china_mirror` 改成 `false` 以使用官方源：
```yaml
china_mirror: true    # 国内环境用镜像源；海外改 false
```

## 第六步：创建 VM

```bash
cd <你的 seed-emulator>/vagrant-deploy
bash scripts/seed_vagrant.sh up
```

首次运行会下载 Ubuntu box 镜像（约 600MB），需要几分钟。

脚本会：
1. 读取 configs/cluster.yaml
2. 自动生成 Vagrantfile
3. 运行 vagrant up 创建所有 VM
4. 验证每台 VM 的 SSH 连通性
5. 导出 SSH 配置到 output/vagrant_ssh_config

成功后会看到：
```
[seed_vagrant] All VMs are up and SSH accessible
```

其他命令：
```bash
bash scripts/seed_vagrant.sh status     # 查看 VM 状态
bash scripts/seed_vagrant.sh ssh master # SSH 到 master
bash scripts/seed_vagrant.sh down       # 销毁所有 VM
```

## 第七步：安装 K3s

```bash
bash scripts/seed_k3s_setup.sh install
```

脚本通过 SSH 自动完成：
1. 验证所有节点 SSH 连通
2. 在 master 安装 K3s server
3. 获取 token，在 worker 上安装 K3s agent
4. 安装 Multus CNI
5. 安装 CNI 插件（macvlan/ipvlan/static）
6. 部署 Docker 私有镜像仓库
7. 检测 CNI 主机网卡
8. 拉取 kubeconfig 到本地
9. 验证集群就绪

成功后会看到：
```
[seed_k3s] K3s cluster setup complete!
[seed_k3s] Kubeconfig: output/kubeconfig.yaml
```

其他命令：
```bash
bash scripts/seed_k3s_setup.sh status   # 查看集群状态
bash scripts/seed_k3s_setup.sh reset    # 卸载 K3s 重来
```

## 第八步：验证

```bash
export KUBECONFIG=$(pwd)/output/kubeconfig.yaml
kubectl get nodes
# 应显示所有节点 Ready
```

## 常见问题

### Vagrant 找不到 VirtualBox / VMware
检查 `configs/env.sh` 是否存在，以及对应 provider 的 PATH 行是否取消了注释。
脚本启动时会报类似：
```
[seed_vagrant] ERROR: Running on WSL but VAGRANT_WSL_ENABLE_WINDOWS_ACCESS is not set.
Did you copy configs/env.sh.example to configs/env.sh and edit it?
```

### SSH 连接超时
VirtualBox → 文件 → 主机网络管理器 → 确保有 192.168.56.1 网络

### K3s 安装失败
确保 VM 能访问互联网。Vagrant 默认会给 VM 加一个 NAT 网卡用于上网。

### 内存不足
减少 VM 数量或内存分配。
