# SEED Emulator vagrant-deploy — Dependencies

按你的 cluster.yaml `provider` 字段(vmware_desktop / virtualbox / libvirt)+ 你的 host 平台(WSL / Linux native / macOS)找对应一栏,**一次性装完**,然后跑 `bash scripts/seed_all.sh up`。

framework 自身只验证依赖(install_deps.sh check),**不替你装 hypervisor / vagrant 本体**。

---

## 0. 通用依赖(所有 provider × 所有平台都要)

| 依赖 | 版本 | 用途 |
|---|---|---|
| `vagrant` | ≥ 2.4 | 框架核心 |
| `python3` | 3.10 – 3.13 | framework + SEED 上游 |
| `ansible` (`ansible-playbook`) | 2.10+ | K3s 安装 playbook |
| `kubectl` | 1.28+ | 集群操作 |
| `ssh` / `scp` | OpenSSH | controller → VM |
| `git` | 任意 | clone repo + submodule |

Python pip 包(framework 跑 `install_deps.sh install` 自动装):见 `configs/requirements.txt`(SEED 上游 + framework override,完整 pinned set)。

---

## 1. provider: `vmware_desktop`

### 1.1 Windows + WSL2

**Windows host 端**:
1. **VMware Workstation Pro 16+**:[官网下载 .exe](https://www.vmware.com/products/workstation-pro/) 装到默认位置或自定义(eg `D:\vmware`)
2. **Vagrant VMware Utility**:[hashicorp .msi](https://developer.hashicorp.com/vagrant/install/vmware) 装(自动启 Windows service)

**WSL2 内**:
```bash
# vagrant
wget -O /tmp/vagrant.deb https://releases.hashicorp.com/vagrant/2.4.9/vagrant_2.4.9-1_amd64.deb
sudo dpkg -i /tmp/vagrant.deb

# vagrant-vmware-desktop plugin
vagrant plugin install vagrant-vmware-desktop

# 通用工具
sudo apt update
sudo apt install -y python3 python3-pip ansible openssh-client git

# kubectl
curl -fsSL --create-dirs -o ~/.local/bin/kubectl \
  https://files.m.daocloud.io/dl.k8s.io/release/v1.28.5/bin/linux/amd64/kubectl
chmod +x ~/.local/bin/kubectl
```

**`configs/provider_settings.yaml`** 填(WSL 调用 Windows 端 vmware):
```yaml
vmware_desktop:
  bin_dir: "/mnt/c/Program Files (x86)/VMware/VMware Workstation"   # 或你自己装的路径
```

### 1.2 Linux native(Ubuntu/Debian)

```bash
# VMware Workstation Pro for Linux (.bundle)
# 从 vmware.com 下载 → sudo bash VMware-Workstation-Full-*.bundle
# 装时附带 Vagrant VMware Utility

# vagrant
sudo apt install -y vagrant
vagrant plugin install vagrant-vmware-desktop

sudo apt install -y python3 python3-pip ansible openssh-client git
curl -fsSL -o ~/.local/bin/kubectl https://dl.k8s.io/release/v1.28.5/bin/linux/amd64/kubectl
chmod +x ~/.local/bin/kubectl
```

`provider_settings.yaml`:
```yaml
vmware_desktop:
  bin_dir: "/usr/bin"
```

### 1.3 macOS(Intel / Apple Silicon)

```bash
# brew 装一切
brew install --cask vmware-fusion vagrant-vmware-utility
brew install vagrant ansible kubernetes-cli python@3.12 bash flock git

# vagrant plugin
vagrant plugin install vagrant-vmware-desktop

# macOS Sonoma+:VMware Utility daemon 自动 launchctl bootstrap(framework install_deps.sh install 跑会自动 ensure)
```

`provider_settings.yaml`:
```yaml
vmware_desktop:
  bin_dir: "/Applications/VMware Fusion.app/Contents/Public"
```

---

## 2. provider: `virtualbox`

### 2.1 Windows + WSL2

**Windows host 端**:
1. **VirtualBox 7.x**:[官网 .exe](https://www.virtualbox.org/wiki/Downloads) 装默认位置(`C:\Program Files\Oracle\VirtualBox\`)

**WSL2 内**:
```bash
# vagrant (同上)
wget -O /tmp/vagrant.deb https://releases.hashicorp.com/vagrant/2.4.9/vagrant_2.4.9-1_amd64.deb
sudo dpkg -i /tmp/vagrant.deb

# 通用工具
sudo apt install -y python3 python3-pip ansible openssh-client git
curl -fsSL --create-dirs -o ~/.local/bin/kubectl https://dl.k8s.io/release/v1.28.5/bin/linux/amd64/kubectl
chmod +x ~/.local/bin/kubectl
```

**注意**:vbox 6.1+ 默认只允许 host-only IP `192.168.56.0/21`。framework `normalize_cluster.py` 已自动按 provider 选 `192.168.56.0/24`(改 `provider: virtualbox` 自动生效)。

`provider_settings.yaml`:
```yaml
virtualbox:
  bin_dir: "/mnt/c/Program Files/Oracle/VirtualBox"
```

### 2.2 Linux native

```bash
sudo apt install -y virtualbox vagrant python3 python3-pip ansible openssh-client git
curl -fsSL -o ~/.local/bin/kubectl https://dl.k8s.io/release/v1.28.5/bin/linux/amd64/kubectl
chmod +x ~/.local/bin/kubectl
```

`provider_settings.yaml`:
```yaml
virtualbox:
  bin_dir: "/usr/bin"
```

### 2.3 macOS

```bash
brew install --cask virtualbox
brew install vagrant ansible kubernetes-cli python@3.12 bash flock git
```

`provider_settings.yaml`:
```yaml
virtualbox:
  bin_dir: "/usr/local/bin"     # Intel
  # 或: bin_dir: "/opt/homebrew/bin"   # Apple Silicon
```

---

## 3. provider: `libvirt`(KVM)

### 3.1 Windows + WSL2(需 nested virtualization)

**前置**:Win11 22H2+ 启用嵌套虚拟化(BIOS 开 VT-x + Win 端 Hyper-V Platform)。

**WSL2 启用 systemd**:
```bash
# WSL 内
echo -e "[boot]\nsystemd=true" | sudo tee /etc/wsl.conf
```
然后 Windows PowerShell 跑 `wsl --shutdown` 然后重开 WSL distro。

**WSL2 内装 KVM + libvirt**:
```bash
# kernel module
sudo modprobe kvm_intel    # AMD 用 kvm_amd
echo kvm_intel | sudo tee /etc/modules-load.d/kvm.conf

# KVM + libvirt + qemu (含 vagrant-libvirt 编译 deps)
sudo apt update
sudo apt install -y \
  qemu-kvm libvirt-daemon-system libvirt-clients libvirt-dev \
  bridge-utils dnsmasq virt-manager \
  build-essential pkg-config ruby-dev

# 加入 libvirt + kvm group
sudo usermod -aG libvirt,kvm $USER     # 重 login WSL 生效

# iptables backend (Ubuntu 22+ 必做 — 默认 nf_tables,libvirt 期望 legacy)
# 不切的话 vagrant up libvirt 报 `table 'filter' is incompatible, use 'nft' tool`
sudo update-alternatives --set iptables /usr/sbin/iptables-legacy
sudo update-alternatives --set ip6tables /usr/sbin/ip6tables-legacy
sudo systemctl restart libvirtd

# vagrant + vagrant-libvirt plugin (编译 ~5-10 min)
wget -O /tmp/vagrant.deb https://releases.hashicorp.com/vagrant/2.4.9/vagrant_2.4.9-1_amd64.deb
sudo dpkg -i /tmp/vagrant.deb
vagrant plugin install vagrant-libvirt

# 通用工具
sudo apt install -y python3 python3-pip ansible openssh-client git
curl -fsSL --create-dirs -o ~/.local/bin/kubectl https://dl.k8s.io/release/v1.28.5/bin/linux/amd64/kubectl
chmod +x ~/.local/bin/kubectl
```

`provider_settings.yaml`:
```yaml
libvirt:
  bin_dir: "/usr/bin"
```

### 3.2 Linux native(Ubuntu/Debian)

```bash
sudo apt update
sudo apt install -y \
  qemu-kvm libvirt-daemon-system libvirt-clients libvirt-dev \
  bridge-utils dnsmasq virt-manager \
  vagrant python3 python3-pip ansible openssh-client git \
  build-essential pkg-config ruby-dev

sudo systemctl enable --now libvirtd
sudo usermod -aG libvirt,kvm $USER     # 重 login

# iptables backend (Ubuntu 22+ 必做)
sudo update-alternatives --set iptables /usr/sbin/iptables-legacy
sudo update-alternatives --set ip6tables /usr/sbin/ip6tables-legacy
sudo systemctl restart libvirtd

vagrant plugin install vagrant-libvirt

curl -fsSL -o ~/.local/bin/kubectl https://dl.k8s.io/release/v1.28.5/bin/linux/amd64/kubectl
chmod +x ~/.local/bin/kubectl
```

`provider_settings.yaml`:
```yaml
libvirt:
  bin_dir: "/usr/bin"
```

### 3.3 macOS

**不推荐** — macOS 上 libvirt + vagrant-libvirt 历史脆弱(brew libvirt-qemu 组合常出问题)。Mac 用户**用 `vmware_desktop` 即可**。

---

## 4. 网络代理(任何 platform / provider 通用)

中国境内访问 docker.io / ghcr.io / get.k3s.io / github.com 等 canonical source 不稳。两条路:

| 模式 | 配置 | framework 行为 |
|---|---|---|
| **有 TUN-mode 全局代理**(mihomo / clash TUN + Allow LAN) | `proxy_settings.yaml`: `system_proxy: true` | framework 走 canonical source,VM 出口透明经 host TUN |
| **无代理 / China 直连** | `system_proxy: false` | framework 走 cn mirror(daocloud / 1ms.run / aliyun / rancher-mirror.cn)+ multi-fallback retry |

`proxy_settings.yaml.example` framework 第一次跑会自动生成。

---

## 5. 装完后 sanity check

```bash
cd vagrant-deploy/

# framework 自检(验证上面的 deps + 自动 ensure 一些 mac/WSL 特殊事)
bash scripts/install_deps.sh check

# 通过后跑全流程
bash scripts/seed_all.sh up
```

---

## 6. 速查总结表

| Platform | vmware_desktop | virtualbox | libvirt |
|---|---|---|---|
| **WSL2** | Win端 VMware Workstation + Utility,WSL 装 vagrant+plugin | Win端 VBox,WSL 装 vagrant | WSL 内装 KVM+libvirt+vagrant-libvirt + systemd + nested virt |
| **Linux native** | .bundle 装 VMware Workstation + Utility | apt 装 virtualbox | apt 装 qemu-kvm+libvirt+vagrant-libvirt |
| **macOS** | brew --cask vmware-fusion + vagrant-vmware-utility | brew --cask virtualbox | ⚠️ 不推荐,改用 vmware_desktop |
