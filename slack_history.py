from slacker import Slacker
import json
import argparse
import os
import shutil
import copy
from datetime import datetime

# This script finds all channels, private channels and direct messages
# that your user participates in, downloads the complete history for
# those converations and writes each conversation out to seperate json files.
#
# This user centric history gathering is nice because the official slack data exporter
# only exports public channels.
#
# PS, this only works if your slack team has a paid account which allows for unlimited history.
#
# PPS, this use of the API is blessed by Slack.
# https://get.slack.help/hc/en-us/articles/204897248
# " If you want to export the contents of your own private groups and direct messages
# please see our API documentation."
#
# get your slack user token at the bottom of this page
# https://api.slack.com/web
#
# dependencies:
#	pip install slacker #https://github.com/os/slacker
#
# usage examples
#	python slack_history.py --token='123token'
#	python slack_history.py --token='123token' --dryRun=True
#	python slack_history.py --token='123token' --skipDirectMessages
#	python slack_history.py --token='123token' --skipDirectMessages --skipPrivateChannels


# fetches the complete message history for a channel/group/im
#
# pageableObject could be:
# slack.channel
# slack.groups
# slack.im
#
# channelId is the id of the channel/group/im you want to download history for.
def getHistory(pageableObject, channelId, pageSize = 100):
	messages = []
	lastTimestamp = None

	while(True):
		response = pageableObject.history(
			channel = channelId,
			latest	= lastTimestamp,
			oldest	= 0,
			count	 = pageSize
		).body

		messages.extend(response['messages'])

		if (response['has_more'] == True):
			lastTimestamp = messages[-1]['ts'] # -1 means last element in a list
		else:
			break
	return messages


def mkdir(directory):
	if not os.path.isdir(directory):
		os.makedirs(directory)


# create datetime object from slack timestamp ('ts') string
def parseTimeStamp( timeStamp ):
	if '.' in timeStamp:
		t_list = timeStamp.split('.')
		if len( t_list ) != 2:
			raise ValueError( 'Invalid time stamp' )
		else:
			return datetime.utcfromtimestamp( float(t_list[0]) )


# move channel files from old directory to one with new channel name
def channelRename( oldRoomName, newRoomName ):
	# check if any files need to be moved
	if not os.path.isdir( oldRoomName ):
		return
	mkdir( newRoomName )
	for fileName in os.listdir( oldRoomName ):
		shutil.move( os.path.join( oldRoomName, fileName ), newRoomName )
	os.rmdir( oldRoomName )


def writeMessageFile( fileName, messages ):
	directory = os.path.dirname(fileName)

	if not os.path.isdir( directory ):
		mkdir( directory )

	with open(fileName, 'w') as outFile:
		json.dump( messages, outFile, indent=4)


# parse messages by date
def parseMessages( parentDir, roomDir, messages, roomType ):
	nameChangeFlag = roomType + "_name"

	currentFileDate = ''
	currentMessages = []
	for message in messages:
		#first store the date of the next message
		ts = parseTimeStamp( message['ts'] )
		fileDate = '{:%Y-%m-%d}'.format(ts)

		#if it's on a different day, write out the previous day's messages
		if fileDate != currentFileDate:
			outFileName = '{parent}/{room}/{file}.json'.format( parent = parentDir, room = roomDir, file = currentFileDate )
			writeMessageFile( outFileName, currentMessages )
			currentFileDate = fileDate
			currentMessages = []

		# check if current message is a name change
		# dms won't have name change events
		if roomType != "im" and ( 'subtype' in message ) and message['subtype'] == nameChangeFlag:
			roomDir = message['name']
			oldRoomPath = '{parent}/{room}'.format( parent = parentDir, room = message['old_name'] )
			newRoomPath = '{parent}/{room}'.format( parent = parentDir, room = roomDir )
			channelRename( oldRoomPath, newRoomPath )

		currentMessages.append( message )
	outFileName = '{parent}/{room}/{file}.json'.format( parent = parentDir, room = roomDir, file = currentFileDate )
	writeMessageFile( outFileName, currentMessages )


# fetch and write history for all public channels
def getChannels(slack, dryRun):
	channels = slack.channels.list().body['channels']

	print("\nfound channels: ")
	for channel in channels:
		print(channel['name'])

	if not dryRun:
		parentDir = "channel"
		mkdir(parentDir)
		for channel in channels:
			print("getting history for channel {0}".format(channel['name']))
			channelDir = channel['name']
			mkdir( os.path.join( parentDir, channelDir ) )
			messages = getHistory(slack.channels, channel['id'])
			parseMessages( parentDir, channelDir, messages, 'channel')


# write channels.json file
def dumpChannelFile( slack ):
	print("Making channels file")
	channels = slack.channels.list().body['channels']

	#have to convert private channels to channels to be read in properly
	groups = slack.groups.list().body['groups']
	print( str(len(channels) ) )
	for group in groups:
		print( str(len(channels) ) )
		new_channel = copy.copy(channels[0])
		new_channel['id'] = group['id']
		new_channel['name'] = group['name']
		new_channel['created'] = group['created']
		new_channel['creator'] = group['creator']
		new_channel['is_archived'] = group['is_archived']
		new_channel['is_channel'] = True
		new_channel['is_general'] = False
		new_channel['is_member'] = True
		new_channel['members'] = group['members']
		new_channel['num_members'] = len(group['members'])
		new_channel['purpose'] = group['purpose']
		new_channel['topic'] = group['topic']
		channels.append( new_channel )

	#We will be overwriting this file on each run.
	with open('channels.json', 'w') as outFile:
		json.dump( channels , outFile, indent=4)


# fetch and write history for all direct message conversations
# also known as IMs in the slack API.
def getDirectMessages(slack, ownerId, userIdNameMap, dryRun):
	dms = slack.im.list().body['ims']

	print("\nfound direct messages (1:1) with the following users:")
	for dm in dms:
		print(userIdNameMap.get(dm['user'], dm['user'] + " (name unknown)"))

	if not dryRun:
		parentDir = "direct_message"
		mkdir(parentDir)
		for dm in dms:
			name = userIdNameMap.get(dm['user'], dm['user'] + " (name unknown)")#note: double check naming of dm directory
			print("getting history for direct messages with {0}".format(name))
			dmDir = name
			mkdir('{parent}/{dm}'.format( parent = parentDir, dm = dmDir ))
			messages = getHistory(slack.im, dm['id'])
			parseMessages( parentDir, dmDir, messages, "im" )


# fetch and write history for all private channels
# also known as groups in the slack API.
def getPrivateChannels(slack, dryRun):
	groups = slack.groups.list().body['groups']

	print("\nfound private channels:")
	for group in groups:
		print("{0}: ({1} members)".format(group['name'], len(group['members'])))

	if not dryRun:
		parentDir = "private_channels"
		mkdir(parentDir)
		for group in groups:
			messages = []
			print("getting history for private channel {0} with id {1}".format(group['name'], group['id']))
			groupDir = group['name']
			mkdir( '{parent}/{group}'.format( parent = parentDir, group = groupDir ) )
			messages = getHistory(slack.groups, group['id'])
			parseMessages( parentDir, groupDir, messages, 'group' )

# fetch all users for the channel and return a map userId -> userName
def getUserMap(slack):
	#get all users in the slack organization
	users = slack.users.list().body['members']
	userIdNameMap = {}
	for user in users:
		userIdNameMap[user['id']] = user['name']
	print("found {0} users ".format(len(users)))
	return userIdNameMap

# stores json of user info
def dumpUserFile(slack):
	#write to user file, any existing file needs to be overwritten.
	with open( "users.json", 'w') as userFile:
		json.dump( slack.users.list().body['members'], userFile, indent=4 )

# get basic info about the slack channel to ensure the authentication token works
def doTestAuth(slack):
	testAuth = slack.auth.test().body
	teamName = testAuth['team']
	currentUser = testAuth['user']
	print("Successfully authenticated for team {0} and user {1} ".format(teamName, currentUser))
	return testAuth

if __name__ == "__main__":
	parser = argparse.ArgumentParser(description='download slack history')

	parser.add_argument('--token', help="an api token for a slack user")

	parser.add_argument(
		'--dryRun',
		action='store_true',
		default=False,
		help="if dryRun is true, don't fetch/write history only get channel names")

	parser.add_argument(
		'--skipPrivateChannels',
		action='store_true',
		default=False,
		help="skip fetching history for private channels")

	parser.add_argument(
		'--skipChannels',
		action='store_true',
		default=False,
		help="skip fetching history for channels")

	parser.add_argument(
		'--skipDirectMessages',
		action='store_true',
		default=False,
		help="skip fetching history for directMessages")

	args = parser.parse_args()

	slack = Slacker(args.token)

	testAuth = doTestAuth(slack)

	userIdNameMap = getUserMap(slack)

	dryRun = args.dryRun

	if not dryRun:
		#write channel and user jsons
		dumpUserFile(slack)
		dumpChannelFile(slack)

	if not args.skipChannels:
		getChannels(slack, dryRun)

	if not args.skipPrivateChannels:
		getPrivateChannels(slack, dryRun)

	if not args.skipDirectMessages:
		getDirectMessages(slack, testAuth['user_id'], userIdNameMap, dryRun)
