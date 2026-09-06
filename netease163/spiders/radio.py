"""
"""
from typing import Dict, Any
from .base import BaseSpider


class RadioSpider(BaseSpider):
    name = "radio"

    def fetch(self, radio_id: int, limit: int = 30, offset: int = 0) -> Dict[str, Any]:
        """
        获取电台节目列表
        """
        from pyncm import GetCurrentSession
        from pyncm.apis import WeapiEncrypt

        session = GetCurrentSession()
        payload = (
            f'{{"id":{radio_id},"offset":{offset},"total":"true",'
            f'"limit":{limit},"csrf_token":""}}'
        )
        resp = session.request(
            "POST",
            f"https://music.163.com/weapi/djradio/by-virtua-radio?csrf_token=",
            params={"csrf_token": ""},
            data={**WeapiEncrypt(payload)},
            headers={"Referer": "https://music.163.com"},
        )
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}")
        data = resp.json()

        programs = [
            {
                "id": p["id"],
                "name": p["name"],
                "dj": p.get("dj", {}).get("nickname", ""),
                "duration": p.get("duration", 0),
                "create_time": p.get("createTime", 0),
            }
            for p in data.get("programs", [])
        ]

        return {
            "radio_id": radio_id,
            "count": len(programs),
            "programs": programs,
        }
