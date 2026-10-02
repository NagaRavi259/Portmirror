"""Forward definition + validation that doesn't need the database."""
from datetime import datetime, timezone
from ipaddress import IPv4Address, IPv4Network
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from . import config

Protocol = Literal["tcp", "udp", "both"]


class ForwardIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    protocol: Protocol = "tcp"
    listen_port: int = Field(ge=1, le=65535)
    listen_port_end: Optional[int] = Field(default=None, ge=1, le=65535)
    target_ip: IPv4Address
    target_port: int = Field(ge=1, le=65535)
    allowed_sources: list[IPv4Network] = Field(default_factory=lambda: [config.LAN_NET])
    rate_limit: Optional[int] = Field(default=None, ge=1, le=100_000, description="max new connections / second")
    max_conns: Optional[int] = Field(default=None, ge=1, le=1_000_000, description="max concurrent connections")
    bandwidth_limit_kbps: Optional[int] = Field(default=None, ge=1, le=10_000_000,
                                                description="max throughput, kbit/s, applied separately to each direction")
    expires_at: Optional[datetime] = None
    enabled: bool = True
    description: str = Field(default="", max_length=500)

    @field_validator("name", "description")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    @field_validator("allowed_sources", mode="before")
    @classmethod
    def _lenient_cidrs(cls, v):
        # accept "192.168.88.5" and host-bit-set CIDRs like "192.168.88.5/24"
        if isinstance(v, list):
            from ipaddress import ip_network
            return [ip_network(str(x).strip(), strict=False) for x in v]
        return v

    @field_validator("expires_at")
    @classmethod
    def _utc(cls, v: Optional[datetime]) -> Optional[datetime]:
        if v is None:
            return v
        return v.replace(tzinfo=timezone.utc) if v.tzinfo is None else v.astimezone(timezone.utc)

    @model_validator(mode="after")
    def _check(self):
        end = self.listen_port_end
        if end is not None and end == self.listen_port:
            self.listen_port_end = end = None
        if end is not None:
            if end < self.listen_port:
                raise ValueError("listen_port_end must be >= listen_port")
            if self.span > config.MAX_RANGE:
                raise ValueError(f"port range too large (max {config.MAX_RANGE} ports)")
            if self.target_port + self.span - 1 > 65535:
                raise ValueError("target port range runs past 65535")
        if self.target_ip not in config.VPN_NET:
            raise ValueError(f"target_ip must be inside the VPN network {config.VPN_NET}")
        if self.target_ip in (config.GW_VPN_IP, config.VPN_NET.network_address, config.VPN_NET.broadcast_address):
            raise ValueError("target_ip can't be the gateway, network or broadcast address")
        if "tcp" in self.protocols:
            clash = config.RESERVED_TCP_PORTS & set(self.listen_ports)
            if clash:
                raise ValueError(f"TCP port {min(clash)} is reserved for the manager UI")
        if not self.allowed_sources:
            raise ValueError("allowed_sources can't be empty")
        return self

    @property
    def protocols(self) -> list[str]:
        return ["tcp", "udp"] if self.protocol == "both" else [self.protocol]

    @property
    def span(self) -> int:
        return (self.listen_port_end or self.listen_port) - self.listen_port + 1

    @property
    def listen_ports(self) -> range:
        return range(self.listen_port, (self.listen_port_end or self.listen_port) + 1)

    @property
    def target_port_end(self) -> int:
        return self.target_port + self.span - 1

    def overlaps(self, other: "ForwardIn") -> bool:
        if not set(self.protocols) & set(other.protocols):
            return False
        a0, a1 = self.listen_port, self.listen_port_end or self.listen_port
        b0, b1 = other.listen_port, other.listen_port_end or other.listen_port
        return a0 <= b1 and b0 <= a1

    def expired(self, now: Optional[datetime] = None) -> bool:
        return self.expires_at is not None and self.expires_at <= (now or datetime.now(timezone.utc))


class Forward(ForwardIn):
    id: int
    created_at: datetime
    updated_at: datetime


class ToggleIn(BaseModel):
    enabled: bool
    kill: bool = False


class LoginIn(BaseModel):
    username: str
    password: str


class PasswordIn(BaseModel):
    current: str
    new: str = Field(min_length=10, max_length=200)


class TokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class DeviceNameIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()


class NotificationStateIn(BaseModel):
    state: Literal["read", "actioned", "dismissed"]


class KillConnIn(BaseModel):
    protocol: Literal["tcp", "udp"]
    src: IPv4Address
    sport: int = Field(ge=1, le=65535)
    dport: int = Field(ge=1, le=65535)


class ImportIn(BaseModel):
    forwards: list[ForwardIn]
    mode: Literal["merge", "replace"] = "merge"
