"""
CLI 入口
支持 9 个爬虫子命令 + login 子命令
"""
import typer
import json
import sys
from pathlib import Path

# 兼容从项目根目录直接 python -m netease163.cli
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from netease163.spiders import (
    LyricSpider, CommentSpider, SearchSpider, SongSpider,
    PlaylistSpider, ArtistSpider, AlbumSpider, RadioSpider, ToplistSpider,
)
from netease163.login import LoginManager

app = typer.Typer(help="netease163 — 网易云音乐搜索")
login_app = typer.Typer(help="登录管理")
app.add_typer(login_app, name="login")


def _print(data):
    """统一输出 JSON"""
    print(json.dumps(data, ensure_ascii=False, indent=2))


@app.command()
def lyric(song_id: int = typer.Option(..., "--id", help="歌曲 ID (e.g. 1062642)")):
    """获取歌曲歌词"""
    _print(LyricSpider().safe_fetch(song_id))


@app.command()
def comment(
    song_id: int = typer.Option(..., "--id", help="歌曲 ID"),
    limit: int = typer.Option(20, "--limit", "-l", help="返回条数"),
    offset: int = typer.Option(0, "--offset", help="分页偏移"),
    hot: bool = typer.Option(False, "--hot", help="只看热门评论"),
):
    """获取歌曲评论"""
    _print(CommentSpider().safe_fetch(song_id, limit=limit, offset=offset, hot_only=hot))


@app.command()
def search(
    keyword: str = typer.Option(..., "--q", "-q", help="搜索关键词"),
    type: str = typer.Option("song", "--type", "-t", help="song/artist/album/playlist/user"),
    limit: int = typer.Option(20, "--limit", "-l"),
):
    """搜索 (歌曲/歌手/专辑/歌单/用户)"""
    _print(SearchSpider().safe_fetch(keyword, search_type=type, limit=limit))


@app.command()
def song(song_id: int = typer.Option(..., "--id", help="歌曲 ID")):
    """获取歌曲详情"""
    _print(SongSpider().safe_fetch(song_id))


@app.command()
def playlist(playlist_id: int = typer.Option(..., "--id", help="歌单 ID")):
    """获取歌单详情 (含前 100 首歌曲)"""
    _print(PlaylistSpider().safe_fetch(playlist_id))


@app.command()
def artist(artist_id: int = typer.Option(..., "--id", help="歌手 ID")):
    """获取歌手详情"""
    _print(ArtistSpider().safe_fetch(artist_id))


@app.command()
def album(album_id: int = typer.Option(..., "--id", help="专辑 ID")):
    """获取专辑详情 (含歌曲列表)"""
    _print(AlbumSpider().safe_fetch(album_id))


@app.command()
def radio(
    radio_id: int = typer.Option(..., "--id", help="电台 ID"),
    limit: int = typer.Option(30, "--limit", "-l"),
):
    """获取电台节目列表"""
    _print(RadioSpider().safe_fetch(radio_id, limit=limit))


@app.command()
def toplist(
    name: str = typer.Option("cloud", "--name", "-n", help="cloud/new/original/..."),
    limit: int = typer.Option(50, "--limit", "-l"),
):
    """获取排行榜歌曲"""
    toplist_id = ToplistSpider.get_toplist_ids.get(name)
    if not toplist_id:
        typer.echo(f"❌ 未知榜单: {name}, 可选: {list(ToplistSpider.get_toplist_ids.keys())}")
        raise typer.Exit(1)
    _print(ToplistSpider().safe_fetch(toplist_id, limit=limit))


# ==================== login 子命令 ====================
@login_app.command("phone")
def login_phone(
    phone: str = typer.Option(..., "--phone", help="手机号"),
    password: str = typer.Option(..., "--password", help="密码"),
):
    """手机密码登录"""
    mgr = LoginManager()
    if mgr.login_with_phone(phone, password):
        typer.echo(f"✅ 登录成功: {mgr.nickname}")
    else:
        typer.echo("❌ 登录失败")
        raise typer.Exit(1)


@login_app.command("email")
def login_email(
    email: str = typer.Option(..., "--email", help="邮箱"),
    password: str = typer.Option(..., "--password", help="密码"),
):
    """邮箱密码登录"""
    mgr = LoginManager()
    if mgr.login_with_email(email, password):
        typer.echo(f"✅ 登录成功: {mgr.nickname}")
    else:
        typer.echo("❌ 登录失败")
        raise typer.Exit(1)


@login_app.command("status")
def login_status():
    """查看当前登录状态"""
    mgr = LoginManager()
    if mgr.is_logged_in:
        typer.echo(f"✅ 已登录: {mgr.nickname} (uid={mgr.user_id})")
    else:
        typer.echo("❌ 未登录")


@login_app.command("logout")
def login_logout():
    """登出"""
    LoginManager().logout()
    typer.echo("✅ 已登出")


if __name__ == "__main__":
    app()
