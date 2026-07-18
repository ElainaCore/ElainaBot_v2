async def reply(event, content=None, **kwargs):
    kwargs['skip_suffix'] = True
    return await event.reply(content, **kwargs)


async def sender_reply(sender, event, content=None, **kwargs):
    kwargs['skip_suffix'] = True
    return await sender.reply(event, content, **kwargs)
