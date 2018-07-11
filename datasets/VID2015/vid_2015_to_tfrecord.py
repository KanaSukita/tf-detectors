import os
import io
import glob
import math
import hashlib
import logging
import utils.dataset_util as dataset_util
import tensorflow as tf

from lxml import etree
from PIL import Image

"""
Usage : python vid_2015_to_tfrecord.py \
        --data_dir=/path/to/VID2015_dataset/ILSVRC \
        --output_path=/tmp/vid2015_tfrecord

XML format(example):
</annotation>
    <folder>ILSVRC2015_VID_train_0000/ILSVRC2015_train_00005009</folder>
    <filename>000008</filename>
    <source>
        <database>ILSVRC_2015</database>
    </source>
    <size>
        <width>1280</width>
        <height>576</height>
    </size>
    <object>
        <trackid>0</trackid>
        <name>n02084071</name>
        <bndbox>
            <xmax>976</xmax>
            <xmin>675</xmin>
            <ymax>451</ymax>
            <ymin>115</ymin>
        </bndbox>
        <occluded>0</occluded>
        <generated>0</generated>
    </object>
</annotation>
"""

flags = tf.app.flags
flags.DEFINE_string('data_dir', '', 'Root directory to raw VID 2015 dataset.')
flags.DEFINE_string('set', 'train', 'Convert training set, validation set.')
flags.DEFINE_string('output_path', './data/VID2015', 'Path to output TFRecord')
flags.DEFINE_integer('start_shard', 0, 'Start index of TFRcord files')
flags.DEFINE_integer('num_shards', 10, 'The number of TFRcord files')
flags.DEFINE_integer('num_examples', -1, 'The number of video to convert to TFRecord file')
FLAGS = flags.FLAGS

SETS = ['train', 'val', 'test']

def gen_shard(examples_list, annotations_dir, out_filename):
    writer = tf.python_io.TFRecordWriter(out_filename)
    for indx, example in enumerate(examples_list):
        xml_pattern = os.path.join(annotations_dir, example + '/*.xml')
        xml_files = glob.glob(xml_pattern)
        dicts = []
        ## process per single frame
        for xml_file in xml_files:
            with tf.gfile.GFile(xml_file, 'r') as fid:
                xml_str = fid.read()
            xml = etree.fromstring(xml_str)
            dic = dataset_util.recursive_parse_xml_to_dict(xml)['annotation']
            dicts.append(dic)
        tf_example = dicts_to_tf_example(dicts)
        writer.write(tf_example.SerializeToString())
    writer.close()
    return

def dicts_to_tf_example(dicts):
    """ Convert XML derived dict to tf.Example proto.
    """
    # Non sequential data
    folder = dicts[0]['folder']
    height = int(dicts[0]['size']['height'])
    width = int(dicts[0]['size']['width'])

    # Get image paths
    imgs_dir = os.path.join(FLAGS.data_dir,
                            'Data/VID/{}'.format(FLAGS.set),
                            folder)
    imgs_path = glob.glob(imgs_dir + '/*.JPEG')

    # Frames Info (image)
    filenames = []
    encodeds = []
    sources = []
    keys = []
    formats = []
    # Frames Info (objects)
    xmins, ymins = [], []
    xmaxs, ymaxs = [], []
    names = []
    occludeds = []
    generateds = []

    # Iterate frames
    for data, img_path in zip(dicts, imgs_path):
        ## open single frame
        with tf.gfile.FastGFile(img_path, 'rb') as fid:
            encoded_jpg = fid.read()
        encoded_jpg_io = io.BytesIO(encoded_jpg)
        image = Image.open(encoded_jpg_io)
        if image.format != 'JPEG':
            raise ValueError('Image format not JPEG')
        key = hashlib.sha256(encoded_jpg).hexdigest()
        ## validation
        assert int(data['size']['height']) == height
        assert int(data['size']['width']) == width
        ## iterate objects
        xmin, ymin = [], []
        xmax, ymax = [], []
        name = []
        occluded = []
        generated = []
        if 'object' in data:
            for obj in data['object']:
                xmin.append(float(obj['bndbox']['xmin']) / width)
                ymin.append(float(obj['bndbox']['ymin']) / height)
                xmax.append(float(obj['bndbox']['xmax']) / width)
                ymax.append(float(obj['bndbox']['ymax']) / height)
                name.append(obj['name'].encode('utf8'))
                occluded.append(int(obj['occluded']))
                generated.append(int(obj['generated']))
        ## append tf_feature to list
        filenames.append(dataset_util.bytes_feature(data['filename'].encode('utf8')))
        encodeds.append(dataset_util.bytes_feature(encoded_jpg))
        sources.append(dataset_util.bytes_feature(data['source']['database'].encode('utf8')))
        keys.append(dataset_util.bytes_feature(key.encode('utf8')))
        formats.append(dataset_util.bytes_feature('jpeg'.encode('utf8')))
        xmins.append(dataset_util.float_list_feature(xmin))
        ymins.append(dataset_util.float_list_feature(ymin))
        xmaxs.append(dataset_util.float_list_feature(xmax))
        ymaxs.append(dataset_util.float_list_feature(ymax))
        occludeds.append(dataset_util.int64_list_feature(occluded))
        generateds.append(dataset_util.int64_list_feature(generated))

    # Non sequential features
    context = tf.train.Features(feature={
        'video/folder': dataset_util.bytes_feature(folder.encode('utf8')),
        'video/frame_numbers': dataset_util.int64_feature(len(imgs_path)),
        'video/height': dataset_util.int64_feature(height),
        'video/width': dataset_util.int64_feature(width),
        })
    # Sequential features
    tf_feature_lists = {
        'image/filename': tf.train.FeatureList(feature=filenames),
        'image/encoded': tf.train.FeatureList(feature=encodeds),
        'image/sources': tf.train.FeatureList(feature=sources),
        'image/key/sha256': tf.train.FeatureList(feature=keys),
        'image/format': tf.train.FeatureList(feature=formats),
        'image/object/bbox/xmin': tf.train.FeatureList(feature=xmins),
        'image/object/bbox/xmax': tf.train.FeatureList(feature=xmaxs),
        'image/object/bbox/ymin': tf.train.FeatureList(feature=ymins),
        'image/object/bbox/ymax': tf.train.FeatureList(feature=ymaxs),
        'image/object/name': tf.train.FeatureList(feature=names),
        'image/object/occluded': tf.train.FeatureList(feature=occludeds),
        'image/object/generated': tf.train.FeatureList(feature=generateds),
        }
    feature_lists = tf.train.FeatureLists(feature_list=tf_feature_lists)
    # Make single sequence example
    tf_example = tf.train.SequenceExample(context=context, feature_lists=feature_lists)
    return tf_example

def main(_):
    data_dir = FLAGS.data_dir

    if FLAGS.set not in SETS:
        raise ValueError('set must be in : {}'.format(SETS))

    # Read Example list files
    logging.info('Reading from VID 2015 dataset. ({})'.format(data_dir))
    list_file_pattern = 'ImageSets/VID/{}*.txt'.format(FLAGS.set)
    examples_paths = glob.glob(os.path.join(data_dir, list_file_pattern))
    examples_list = []
    for examples_path in examples_paths:
        examples_list.extend(dataset_util.read_examples_list(examples_path))
    if FLAGS.num_examples > 0:
        examples_list = examples_list[:FLAGS.num_examples]

    # Sharding
    start_shard = FLAGS.start_shard
    num_shards = FLAGS.num_shards
    num_digits = math.ceil(math.log10(num_shards-1))
    shard_format = '%0'+ ('%d'%num_digits) + 'd'
    examples_per_shard = int(math.ceil(len(examples_list)/float(num_shards)))
    annotations_dir = os.path.join(data_dir,
                                   'Annotations/VID/{}'.format(FLAGS.set))
    # Generate each shard
    for i in range(start_shard, num_shards):
        start = i * examples_per_shard
        end = (i+1) * examples_per_shard
        out_filename = os.path.join(FLAGS.output_path,
                'VID_2015-'+(shard_format % i)+'.tfrecord')
        if os.path.isfile(out_filename): # Don't recreate data if restarting
            continue
        print (str(i)+'of'+str(num_shards)+'['+str(start)+':'+str(end),']'+out_filename)
        gen_shard(examples_list[start:end], annotations_dir, out_filename)
    # Clean up writing last shard
    start = num_shards * examples_per_shard
    out_filename = os.path.join(FLAGS.output_path,
            'VID_2015-'+(shard_format % num_shards)+'.tfrecord')
    print (str(i)+'of',str(num_shards)+'['+str(start)+':]'+out_filename)
    gen_shard(examples_list[start:], annotations_dir, out_filename)
    return

if __name__ == '__main__':
  tf.app.run()
