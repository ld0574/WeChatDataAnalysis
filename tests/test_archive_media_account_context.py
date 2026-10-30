import pytest

from wechat_decrypt_tool import live_archive_media as media


def test_image_cache_survives_retired_account_directory(tmp_path, monkeypatch):
    from wechat_decrypt_tool import app_paths

    legacy = tmp_path / 'old-databases'
    monkeypatch.setattr(app_paths, 'get_output_databases_dir', lambda: legacy)
    context = tmp_path / 'media-accounts' / 'account'
    md5 = 'a' * 32
    cached = context / 'resource' / 'aa' / (md5 + '.png')
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b'preserved image bytes')
    config = {'baseline': str(tmp_path / 'baseline'), 'source_account': 'account',
              'media_account_dir': str(context)}
    assert not legacy.exists()
    assert media.local_image_path(config, {}, md5) == cached


def test_media_identity_cannot_silently_use_another_accounts_keys(tmp_path):
    config = {'source_account': 'account', 'media_account_dir': str(tmp_path / 'other')}
    with pytest.raises(ValueError, match='source_account'):
        media.media_account_dir(config)
