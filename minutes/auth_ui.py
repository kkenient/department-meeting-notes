"""Authentication runs before any meeting storage or meeting widgets are loaded."""

import streamlit as st

from .auth import Accounts, AuthError


def require_login(settings):
    if not settings.auth_required:
        return
    accounts = Accounts(settings.data_dir / "accounts.sqlite3")
    token = st.session_state.get("auth_token", "")
    username = accounts.user(token)
    if username:
        with st.sidebar:
            st.caption(f"已登录：{username} · 部门共享会议库")
            if st.button("退出登录"):
                accounts.logout(token)
                st.session_state.clear()
                st.query_params.clear()
                st.rerun()
        return
    # Remove prior meeting/widget data when a session expires; do not keep passwords.
    if token:
        st.session_state.clear()
        st.query_params.clear()
    st.title("会记 · 部门会议库")
    st.write("登录后可查看、编辑和删除部门共享会议；首次使用请向管理员索取邀请码。")
    login, register = st.tabs(["登录", "注册"])
    with login:
        with st.form("login_form", clear_on_submit=True):
            name = st.text_input("账号", key="login_name", max_chars=32)
            password = st.text_input("密码", type="password", key="login_password", max_chars=128)
            if st.form_submit_button("登录"):
                try:
                    st.session_state.auth_token = accounts.login(name, password)
                    st.rerun()
                except AuthError as exc:
                    st.error(str(exc))
    with register:
        with st.form("register_form", clear_on_submit=True):
            name = st.text_input("注册账号", max_chars=32)
            password = st.text_input("设置密码", type="password", max_chars=128)
            repeated = st.text_input("再次输入密码", type="password", max_chars=128)
            invite = st.text_input("部门邀请码", type="password", max_chars=256)
            st.caption("账号使用 3–32 位英文或数字等字符，密码至少 12 个字符。")
            if st.form_submit_button("注册账号"):
                try:
                    if password != repeated:
                        raise AuthError("两次输入的密码不一致。")
                    accounts.register(name, password, invite, settings.registration_invite)
                    st.success("注册成功，请切换到登录页登录。")
                except AuthError as exc:
                    st.error(str(exc))
    st.stop()
