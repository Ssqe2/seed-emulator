FROM 98a2693c996c2294358552f48373498d
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends netcat-openbsd
RUN apt-get update && apt-get install -y --no-install-recommends bind9
COPY 92872f20cfb75af4e3e1c588b00d6484 /replace_address.sh
COPY 365d27384adc9719099d1f63f2d15824 /root/.zshrc.pre
COPY 082b96ec819c95ae773daebde675ef80 /start.sh
COPY d18858afc6bb66ec3a19d872077acfd2 /seedemu_sniffer
COPY 17ac2d812a99a91e7f747e1defb72a29 /seedemu_worker
RUN chmod +x /start.sh
RUN chmod +x /seedemu_sniffer
RUN chmod +x /seedemu_worker
COPY e01e36443f9f72c6204189260d0bd276 /ifinfo.txt
COPY d3d51fdf7f4bad30dc5db560a01ce629 /interface_setup
COPY 9fa3998da182b9c5f98cb4ba6ea96a2e /etc/bind/named.conf.options
COPY 10012e957e1896df5bbcc7fc8f43d0cb /etc/bind/named.conf.zones
CMD ["/start.sh"]
